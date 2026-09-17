"""Build independent identities for prepared app copies without reading user data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import re
import uuid


SOURCE_BUNDLE_ID = "com.tencent.xinWeChat"
BUNDLE_ID_PREFIX = "local.wechattool.wechat."
_INSTANCE_ID = re.compile(r"[0-9a-f]{32}\Z")
_TEAM_IDENTIFIER = re.compile(r"[A-Za-z0-9]{10}\.\Z")
_APPLICATION_IDS = ("application-identifier", "com.apple.application-identifier")
_GROUPS = "com.apple.security.application-groups"
_MACH_LOOKUP = "com.apple.security.temporary-exception.mach-lookup.global-name"
_FILE_PROVIDER_SUFFIX = ".WeChatFileProviderExtension"
_DOCUMENT_GROUP = "NSExtensionFileProviderDocumentGroup"


@dataclass(frozen=True)
class InstanceIdentity:
    instance_id: str
    bundle_id: str
    source_bundle_id: str
    team_identifier: str
    app_group: str

    def __post_init__(self) -> None:
        if not isinstance(self.instance_id, str) or not _INSTANCE_ID.fullmatch(self.instance_id):
            raise ValueError("Instance ID must contain exactly 32 lowercase hexadecimal characters.")
        if not isinstance(self.bundle_id, str) or self.bundle_id != BUNDLE_ID_PREFIX + self.instance_id:
            raise ValueError("Bundle identifier does not match the generated instance ID.")
        if self.source_bundle_id != SOURCE_BUNDLE_ID:
            raise ValueError("Expected the original macOS WeChat bundle identifier.")
        if not isinstance(self.team_identifier, str) or not _TEAM_IDENTIFIER.fullmatch(self.team_identifier):
            raise ValueError("TeamIdentifier must contain ten letters/digits followed by a period.")
        if not isinstance(self.app_group, str) or self.app_group != self.application_identifier:
            raise ValueError("Application group does not match the independent app identity.")

    @property
    def application_identifier(self) -> str:
        return self.team_identifier + self.bundle_id

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _source_metadata(source_info: dict) -> tuple[str, str]:
    if not isinstance(source_info, dict):
        raise ValueError("Source Info.plist must contain a dictionary.")
    source_id = source_info.get("CFBundleIdentifier")
    if not isinstance(source_id, str) or source_id != SOURCE_BUNDLE_ID:
        raise ValueError("An independent copy must be prepared from the original WeChat app.")
    team = source_info.get("TeamIdentifier")
    if not isinstance(team, str) or not _TEAM_IDENTIFIER.fullmatch(team):
        raise ValueError("Source Info.plist has an unsupported TeamIdentifier.")
    return source_id, team


def create_identity(source_info: dict, *, instance_id: str | None = None) -> InstanceIdentity:
    """Allocate a fresh identity; explicit IDs support deterministic preparation tests."""
    source_id, team = _source_metadata(source_info)
    identifier = uuid.uuid4().hex if instance_id is None else instance_id
    if not isinstance(identifier, str) or not _INSTANCE_ID.fullmatch(identifier):
        raise ValueError("Instance ID must contain exactly 32 lowercase hexadecimal characters.")
    bundle_id = BUNDLE_ID_PREFIX + identifier
    return InstanceIdentity(identifier, bundle_id, source_id, team, team + bundle_id)


def _checked_identity(identity: InstanceIdentity) -> None:
    if not isinstance(identity, InstanceIdentity):
        raise ValueError("Expected a validated independent app identity.")
    identity.__post_init__()


def isolated_info(source_info: dict, identity: InstanceIdentity, display_name: str) -> dict:
    """Return main-app metadata; nested helpers require separate preparation."""
    _checked_identity(identity)
    source_id, team = _source_metadata(source_info)
    if (source_id, team) != (identity.source_bundle_id, identity.team_identifier):
        raise ValueError("Independent identity does not belong to this source app.")
    if (not isinstance(display_name, str) or not display_name.strip()
            or "/" in display_name or any(ord(character) < 32 or ord(character) == 127 for character in display_name)):
        raise ValueError("Copy display name must be a nonempty app name without path separators or control characters.")
    result = deepcopy(source_info)
    result.update({
        "CFBundleIdentifier": identity.bundle_id,
        "CFBundleName": display_name,
        "CFBundleDisplayName": display_name,
        "WeChatToolInstanceID": identity.instance_id,
        "WeChatToolSourceBundleIdentifier": identity.source_bundle_id,
        "SUEnableAutomaticChecks": False,
        "SUAutomaticallyUpdate": False,
        "SUAllowsAutomaticUpdates": False,
    })
    result.pop("CFBundleURLTypes", None)
    return result


def _string_list(entitlements: dict, key: str) -> list[str]:
    value = entitlements[key]
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or not item for item in value)):
        raise ValueError(f"Source entitlement {key} must be an array of nonempty strings.")
    return value


def isolated_entitlements(source_entitlements: dict, identity: InstanceIdentity) -> dict:
    """Preserve capabilities while replacing source-app identity and shared grants."""
    _checked_identity(identity)
    if not isinstance(source_entitlements, dict):
        raise ValueError("Source entitlements must contain a dictionary.")
    if source_entitlements.get("com.apple.security.app-sandbox") is not True:
        raise ValueError("Independent copies require an explicitly sandboxed source app.")
    source_app_id = identity.team_identifier + identity.source_bundle_id
    for key in _APPLICATION_IDS:
        if key in source_entitlements and source_entitlements[key] != source_app_id:
            raise ValueError(f"Source entitlement {key} does not match the source app identity.")
    for key in (_GROUPS, "keychain-access-groups", _MACH_LOOKUP):
        if key in source_entitlements:
            _string_list(source_entitlements, key)
    result = deepcopy(source_entitlements)
    # An ad-hoc signature has no developer team. Retaining this entitlement can
    # make macOS reject the process before its entry point runs.
    result.pop("com.apple.developer.team-identifier", None)
    # The macOS application identity is always explicit, including fixtures and
    # source distributions that omit it. Preserve the alternate key if present.
    result["com.apple.application-identifier"] = identity.application_identifier
    if "application-identifier" in result:
        result["application-identifier"] = identity.application_identifier
    result[_GROUPS] = [identity.app_group]
    if "keychain-access-groups" in result:
        result["keychain-access-groups"] = [identity.application_identifier]
    if _MACH_LOOKUP in result:
        result[_MACH_LOOKUP] = [
            identity.bundle_id + name[len(identity.source_bundle_id):]
            if (name == identity.source_bundle_id
                or name.startswith(identity.source_bundle_id + "-")
                or name.startswith(identity.source_bundle_id + "."))
            else name
            for name in result[_MACH_LOOKUP]
        ]
    return result


def isolated_file_provider_info(source_info: dict, identity: InstanceIdentity) -> dict:
    """Keep FileProvider attached exclusively to its independent host app group."""
    _checked_identity(identity)
    if not isinstance(source_info, dict):
        raise ValueError("FileProvider Info.plist must contain a dictionary.")
    if source_info.get("CFBundleIdentifier") != identity.source_bundle_id + _FILE_PROVIDER_SUFFIX:
        raise ValueError("Unexpected source FileProvider bundle identifier.")
    if source_info.get("TeamIdentifier") != identity.team_identifier:
        raise ValueError("FileProvider TeamIdentifier does not match its source host.")
    extension = source_info.get("NSExtension")
    if (not isinstance(extension, dict)
            or extension.get("NSExtensionPointIdentifier") != "com.apple.fileprovider-nonui"):
        raise ValueError("Expected the non-UI FileProvider extension.")
    source_group = identity.team_identifier + identity.source_bundle_id
    # The actual extension declares its document group directly in NSExtension.
    # Validate and rewrite duplicate declarations in other plist locations too;
    # never leave a second declaration pointing at the original application's data.
    if extension.get(_DOCUMENT_GROUP) != source_group:
        raise ValueError("FileProvider document group does not match its source host.")
    attributes = extension.get("NSExtensionAttributes", {})
    if not isinstance(attributes, dict):
        raise ValueError("FileProvider extension attributes must contain a dictionary.")
    for metadata in (source_info, attributes):
        if _DOCUMENT_GROUP in metadata and metadata[_DOCUMENT_GROUP] != source_group:
            raise ValueError("FileProvider contains an unexpected document-group declaration.")
    result = deepcopy(source_info)
    result["CFBundleIdentifier"] = identity.bundle_id + _FILE_PROVIDER_SUFFIX
    result["NSExtension"][_DOCUMENT_GROUP] = identity.app_group
    for metadata in (result, result["NSExtension"].get("NSExtensionAttributes", {})):
        if _DOCUMENT_GROUP in metadata:
            metadata[_DOCUMENT_GROUP] = identity.app_group
    return result


def isolated_file_provider_entitlements(source_entitlements: dict, identity: InstanceIdentity) -> dict:
    """Preserve extension capabilities with its own identity and only its host group."""
    _checked_identity(identity)
    if not isinstance(source_entitlements, dict):
        raise ValueError("FileProvider entitlements must contain a dictionary.")
    source_group = identity.team_identifier + identity.source_bundle_id
    if _GROUPS not in source_entitlements or _string_list(source_entitlements, _GROUPS) != [source_group]:
        raise ValueError("FileProvider must grant only its original host's application group.")
    source_extension_id = source_group + _FILE_PROVIDER_SUFFIX
    for key in _APPLICATION_IDS:
        if key in source_entitlements and source_entitlements[key] != source_extension_id:
            raise ValueError(f"Source FileProvider entitlement {key} has an unexpected identity.")
    # Reuse host permission validation and shared-grant removal without asking it
    # to interpret an extension's distinct application identifier.
    normalized = deepcopy(source_entitlements)
    for key in _APPLICATION_IDS:
        if key in normalized:
            normalized[key] = source_group
    result = isolated_entitlements(normalized, identity)
    application_id = identity.application_identifier + _FILE_PROVIDER_SUFFIX
    result["com.apple.application-identifier"] = application_id
    if "application-identifier" in result:
        result["application-identifier"] = application_id
    if "keychain-access-groups" in result:
        result["keychain-access-groups"] = [application_id]
    return result
