"""Synthetic fixtures exercise parser failures without touching an installed app."""

import struct
import unittest

from wechattool.macho import MachO, MachOError, inject_dylib


VM_BASE = 0x100000000


def uleb(value):
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(result)


def make_thin(*, arch="arm64", text=b"\x1f\x20\x03\xd5" * 64,
              text_offset=0x400, text_size=None, starts=(0x400,),
              function_data=None, extra_commands=(), uuid_bytes=bytes(range(16))):
    """Return a 0x1000-byte executable with __text and __LINKEDIT segments.

    VM addresses use VM_BASE; text_offset and starts are relative to VM_BASE.
    starts=None omits LC_FUNCTION_STARTS. function_data overrides encoded starts.
    Extra commands are complete, eight-byte-aligned load-command byte strings.
    """
    text_size = len(text) if text_size is None else text_size
    if len(text) > text_size:
        raise ValueError("text does not fit text_size")
    section = struct.pack("<16s16sQQ8I", b"__text", b"__TEXT", VM_BASE + text_offset,
                          text_size, text_offset, 2, 0, 0, 0x80000400, 0, 0, 0)
    text_segment = struct.pack("<II16sQQQQ4I", 0x19, 152, b"__TEXT", VM_BASE,
                               0x1000, 0, 0x800, 5, 5, 1, 0) + section
    linkedit = struct.pack("<II16sQQQQ4I", 0x19, 72, b"__LINKEDIT", VM_BASE + 0x1000,
                           0x1000, 0x800, 0x800, 1, 1, 0, 0)
    commands = [text_segment, linkedit]
    if uuid_bytes is not None:
        commands.append(struct.pack("<II", 0x1B, 24) + uuid_bytes)
    if starts is not None:
        if function_data is None:
            previous = 0
            function_data = b""
            for address in starts:
                function_data += uleb(address - previous)
                previous = address
            function_data += b"\0"
        commands.append(struct.pack("<IIII", 0x26, 16, 0x800, len(function_data)))
    commands.extend(extra_commands)
    command_bytes = b"".join(commands)
    if text_offset < 32 + len(command_bytes) or text_offset + text_size > 0x800:
        raise ValueError("fixture text overlaps headers or linkedit")
    cpu = {"arm64": 0x0100000C, "x86_64": 0x01000007}[arch]
    subtype = 0 if arch == "arm64" else 3
    result = bytearray(0x1000)
    result[:32] = struct.pack("<8I", 0xFEEDFACF, cpu, subtype, 2, len(commands), len(command_bytes), 0, 0)
    result[32:32 + len(command_bytes)] = command_bytes
    result[text_offset:text_offset + len(text)] = text
    if starts is not None:
        result[0x800:0x800 + len(function_data)] = function_data
    return bytes(result)


def make_fat(*images, fat64=False):
    if not images:
        images = (make_thin(arch="arm64"), make_thin(arch="x86_64"))
    offsets = [0x1000 * (index + 1) for index in range(len(images))]
    result = bytearray(offsets[-1] + len(images[-1]))
    result[:8] = struct.pack(">II", 0xCAFEBABF if fat64 else 0xCAFEBABE, len(images))
    for index, (image, offset) in enumerate(zip(images, offsets)):
        cpu, subtype = struct.unpack_from("<II", image, 4)
        entry = (struct.pack(">IIQQII", cpu, subtype, offset, len(image), 12, 0) if fat64
                 else struct.pack(">5I", cpu, subtype, offset, len(image), 12))
        entry_start = 8 + index * len(entry)
        result[entry_start:entry_start + len(entry)] = entry
        result[offset:offset + len(image)] = image
    return bytes(result)


def changed(data, offset, fmt, *values):
    result = bytearray(data)
    struct.pack_into(fmt, result, offset, *values)
    return bytes(result)


class MachOParserTests(unittest.TestCase):
    def test_thin_metadata_and_file_mapping(self):
        image = MachO(make_thin(starts=(0x400, 0x410))).slices[0]
        self.assertEqual(image.arch, "arm64")
        self.assertEqual(image.uuid, "00010203-0405-0607-0809-0a0b0c0d0e0f")
        self.assertEqual(image.function_starts, {VM_BASE + 0x400, VM_BASE + 0x410})
        section = image.sections[0]
        self.assertEqual((section.segment, section.name, section.offset, section.size),
                         ("__TEXT", "__text", 0x400, 0x100))
        self.assertEqual(image.vm_to_offset(VM_BASE + 0x410, 16), 0x410)

    def test_fat32_and_fat64_absolute_offsets(self):
        for fat64 in (False, True):
            with self.subTest(fat64=fat64):
                images = MachO(make_fat(fat64=fat64)).slices
                self.assertEqual([s.arch for s in images], ["arm64", "x86_64"])
                self.assertEqual([s.offset for s in images], [0x1000, 0x2000])
                self.assertEqual(images[1].sections[0].offset, 0x2400)
                self.assertEqual(images[1].vm_to_offset(VM_BASE + 0x410), 0x2410)

    def test_optional_uuid_and_function_starts(self):
        image = MachO(make_thin(uuid_bytes=None, starts=None)).slices[0]
        self.assertIsNone(image.uuid)
        self.assertEqual(image.function_starts, set())

    def test_truncated_headers(self):
        for data in (b"", b"\xcf\xfa\xed\xfe", make_thin()[:31], b"\xca\xfe\xba\xbe", make_fat()[:25]):
            with self.subTest(length=len(data)), self.assertRaises(MachOError):
                MachO(data)

    def test_unsupported_cpu(self):
        for data in (changed(make_thin(), 4, "<I", 7), changed(make_fat(), 8, ">I", 7)):
            with self.assertRaisesRegex(MachOError, "CPU"):
                MachO(data)

    def test_bad_load_commands(self):
        malformed = [changed(make_thin(), 20, "<I", 0xFFFFFFFF),
                     changed(make_thin(), 16, "<I", 0xFFFFFFFF),
                     changed(make_thin(), 36, "<I", 0),
                     changed(make_thin(), 36, "<I", 153),
                     changed(make_thin(), 36, "<I", 0x1000),
                     changed(make_thin(), 16, "<I", 3),
                     changed(make_thin(), 32 + 64, "<I", 2)]
        for data in malformed:
            with self.subTest(header=data[16:40]), self.assertRaises(MachOError):
                MachO(data)

    def test_section_cannot_overlap_commands_or_leave_segment(self):
        section = 32 + 72
        for data in (changed(make_thin(), section + 48, "<I", 64),
                     changed(make_thin(), section + 48, "<I", 0x1000),
                     changed(make_thin(), section + 40, "<Q", 0xFFFFFFFFFFFFFFFF),
                     changed(make_thin(), section + 32, "<Q", VM_BASE - 1)):
            with self.assertRaises(MachOError):
                MachO(data)

    def test_segment_ranges_cannot_overlap(self):
        linkedit = 32 + 152
        for data in (changed(make_thin(), linkedit + 24, "<Q", VM_BASE + 0x800),
                     changed(make_thin(), linkedit + 40, "<Q", 0x400)):
            with self.assertRaisesRegex(MachOError, "Overlapping"):
                MachO(data)

    def test_sections_cannot_overlap(self):
        data = bytearray(make_thin(starts=None))
        old_end = 32 + struct.unpack_from("<I", data, 20)[0]
        section = bytes(data[104:184])
        data[184 + 80:old_end + 80] = data[184:old_end]
        data[184:264] = section
        struct.pack_into("<I", data, 36, 232)
        struct.pack_into("<I", data, 96, 2)
        struct.pack_into("<I", data, 20, old_end - 32 + 80)
        with self.assertRaisesRegex(MachOError, "Overlapping section"):
            MachO(data)

    def test_fat_slice_bounds_overlap_and_cpu_mismatch(self):
        original = make_fat()
        malformed = [changed(original, 16, ">I", 0),
                     changed(original, 20, ">I", 0xFFFFFFFF),
                     changed(original, 36, ">I", 0x1000),
                     changed(original, 24, ">I", 64),
                     changed(original, 12, ">I", 1)]
        for data in malformed:
            with self.assertRaises(MachOError):
                MachO(data)

    def test_fat64_huge_offsets_and_reserved_field(self):
        for data in (changed(make_fat(fat64=True), 16, ">Q", 0xFFFFFFFFFFFFFFFF),
                     changed(make_fat(fat64=True), 36, ">I", 1)):
            with self.assertRaises(MachOError):
                MachO(data)

    def test_vm_mapping_rejects_unbacked_and_out_of_bounds_ranges(self):
        image = MachO(make_thin()).slices[0]
        for address, length in ((VM_BASE - 1, 1), (VM_BASE + 0x900, 1),
                                (VM_BASE + 0x4FF, 2), (VM_BASE, 0), (-1, 1),
                                (0xFFFFFFFFFFFFFFFF, 2)):
            with self.subTest(address=address, length=length), self.assertRaises(MachOError):
                image.vm_to_offset(address, length)

    def test_zerofill_section_is_not_file_backed(self):
        data = changed(make_thin(starts=None), 104 + 64, "<I", 1)
        image = MachO(data).slices[0]
        self.assertFalse(image.sections[0].file_backed)
        with self.assertRaises(MachOError):
            image.vm_to_offset(VM_BASE + 0x400)

    def test_encrypted_images_rejected(self):
        encryption = struct.pack("<6I", 0x2C, 24, 0x400, 0x100, 1, 0)
        with self.assertRaisesRegex(MachOError, "Encrypted"):
            MachO(make_thin(extra_commands=(encryption,)))
        unencrypted = changed(encryption, 16, "<I", 0)
        self.assertEqual(MachO(make_thin(extra_commands=(unencrypted,))).slices[0].arch, "arm64")

    def test_function_start_failures(self):
        malformed = (b"\x80", b"\x80" * 10 + b"\0", b"\xff" * 9 + b"\x02\0",
                     uleb(0x400), uleb(0x400) + b"\0\x01", uleb(0x2000) + b"\0",
                     uleb(16) + b"\0", uleb(0x900) + b"\0")
        for payload in malformed:
            with self.subTest(payload=payload), self.assertRaises(MachOError):
                MachO(make_thin(function_data=payload))

    def test_function_start_data_is_bounded(self):
        # Starts command follows two segments and UUID.
        command = 32 + 152 + 72 + 24
        for data in (changed(make_thin(), command + 8, "<I", 0xFFF),
                     changed(make_thin(), command + 8, "<I", 0),
                     changed(make_thin(), command + 12, "<I", 0xFFFFFFFF)):
            with self.assertRaises(MachOError):
                MachO(data)

    def test_duplicate_uuid_or_starts_rejected(self):
        commands = (struct.pack("<II", 0x1B, 24) + bytes(16),
                    struct.pack("<4I", 0x26, 16, 0x800, 1))
        for command in commands:
            with self.assertRaisesRegex(MachOError, "[Dd]uplicate"):
                MachO(make_thin(extra_commands=(command,)))


class DylibInjectionTests(unittest.TestCase):
    name = "@executable_path/../Frameworks/WeChatTool.dylib"

    def test_injects_all_architectures_and_preserves_code(self):
        for original in (make_thin(), make_fat(), make_fat(fat64=True)):
            with self.subTest(length=len(original)):
                result = inject_dylib(original, self.name)
                self.assertEqual(len(result), len(original))
                for before, after in zip(MachO(original).slices, MachO(result).slices):
                    self.assertIn(self.name, after.dylibs)
                    self.assertEqual(after.ncmds, before.ncmds + 1)
                    self.assertEqual(after.uuid, before.uuid)
                    self.assertEqual(after.function_starts, before.function_starts)
                    self.assertEqual(result[before.offset + 0x400:before.offset + before.size],
                                     original[before.offset + 0x400:before.offset + before.size])

    def test_injection_is_idempotent(self):
        once = inject_dylib(make_fat(), self.name)
        self.assertEqual(inject_dylib(once, self.name), once)

    def test_mixed_already_injected_slices(self):
        arm = inject_dylib(make_thin(), self.name)
        original = make_fat(arm, make_thin(arch="x86_64"))
        result = inject_dylib(original, self.name)
        self.assertEqual(result[0x1000:0x2000], arm)
        self.assertTrue(all(self.name in s.dylibs for s in MachO(result).slices))

    def test_insufficient_header_padding(self):
        data = make_thin(text_offset=0x128, starts=(0x128,))
        with self.assertRaisesRegex(MachOError, "Insufficient header padding"):
            inject_dylib(data, self.name)

    def test_nonzero_padding_is_not_overwritten(self):
        original = bytearray(make_thin())
        end = MachO(original).slices[0].commands_end
        original[end] = 1
        with self.assertRaisesRegex(MachOError, "not zero-filled"):
            inject_dylib(original, self.name)
        self.assertEqual(original[end], 1)

    def test_failure_in_second_slice_does_not_edit_input(self):
        original = bytearray(make_fat(make_thin(), make_thin(arch="x86_64", text_offset=0x128, starts=(0x128,))))
        snapshot = bytes(original)
        with self.assertRaises(MachOError):
            inject_dylib(original, self.name)
        self.assertEqual(original, snapshot)

    def test_no_file_backed_sections_cannot_be_injected(self):
        data = changed(make_thin(starts=None), 104 + 64, "<I", 1)
        with self.assertRaisesRegex(MachOError, "no file-backed sections"):
            inject_dylib(data, self.name)

    def test_signature_command_and_data_preserved(self):
        signature = struct.pack("<4I", 0x1D, 16, 0x900, 0x100)
        original = bytearray(make_thin(extra_commands=(signature,)))
        original[0x900:0xA00] = b"S" * 0x100
        result = inject_dylib(original, self.name)
        self.assertEqual(result[0x900:0xA00], original[0x900:0xA00])
        self.assertEqual(sum(c.cmd == 0x1D for c in MachO(result).slices[0].load_commands), 1)

    def test_invalid_install_name(self):
        for name in ("", "a\0b", "\ud800", None):
            with self.subTest(name=repr(name)), self.assertRaises(MachOError):
                inject_dylib(make_thin(), name)

    def test_malformed_existing_dylib_name_is_rejected(self):
        for nameoff, raw in ((23, b"x\0"), (100, b"x\0"), (24, b"x" * 8)):
            command = struct.pack("<6I", 0xC, 32, nameoff, 0, 0, 0) + raw.ljust(8, b"\0")
            with self.assertRaises(MachOError):
                MachO(make_thin(extra_commands=(command,)))


if __name__ == "__main__":
    unittest.main()
