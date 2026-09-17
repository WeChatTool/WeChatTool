"""Conservative, dependency-free parsing and load-command editing of Mach-O files.

Offsets exposed by this module are absolute offsets in the input file, including
for universal binaries.  On-disk offsets in load commands remain slice-relative.
The editor only consumes existing zero-filled header padding; it never moves code.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
import uuid as uuid_module


class MachOError(ValueError):
    """A malformed, unsupported, or unsafe-to-edit Mach-O image."""


_CPU_ARCH = {0x01000007: "x86_64", 0x0100000C: "arm64"}
_MH_MAGIC_64 = b"\xcf\xfa\xed\xfe"
_FAT_MAGIC = b"\xca\xfe\xba\xbe"
_FAT_MAGIC_64 = b"\xca\xfe\xba\xbf"
_UINT64_MAX = (1 << 64) - 1
_ZEROFILL_TYPES = {0x1, 0xC, 0x12}
_DYLIB_LOAD_COMMANDS = {0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023}
_LINKEDIT_DATA_COMMANDS = {0x1D, 0x1E, 0x26, 0x29, 0x2B, 0x2E, 0x80000033, 0x80000034}


@dataclass(frozen=True)
class Section:
    segment: str
    name: str
    addr: int
    size: int
    offset: int
    flags: int
    file_backed: bool


@dataclass(frozen=True)
class Segment:
    name: str
    addr: int
    size: int
    offset: int
    file_size: int
    flags: int
    maxprot: int
    initprot: int


@dataclass(frozen=True)
class LoadCommand:
    cmd: int
    offset: int
    size: int


def _name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="backslashreplace")


def _check_disjoint(ranges: list[tuple[int, int, str]], description: str) -> None:
    previous_end = -1
    previous_name = ""
    for start, end, name in sorted(ranges):
        if start == end:
            continue
        if start < previous_end:
            raise MachOError(f"Overlapping {description}: {previous_name} and {name}")
        previous_end, previous_name = end, name


class Slice:
    """One little-endian, 64-bit x86_64 or arm64 Mach-O image."""

    header_size = 32

    def __init__(self, data: bytes, offset: int, size: int):
        self._data = data
        self.offset = offset
        self.size = size
        self.uuid: str | None = None
        self.sections: list[Section] = []
        self.segments: list[Segment] = []
        self.load_commands: list[LoadCommand] = []
        self.function_starts: set[int] = set()
        self.dylibs: list[str] = []
        self._payload_ranges: list[tuple[int, int, str]] = []
        self._function_data: tuple[int, int] | None = None
        self._parse()

    @property
    def commands_end(self) -> int:
        """Absolute first byte after the existing load commands."""
        return self.offset + self.header_size + self.sizeofcmds

    def _range(self, relative: int, length: int, what: str) -> int:
        if relative < 0 or length < 0 or relative > self.size or length > self.size - relative:
            raise MachOError(f"{what} extends outside {getattr(self, 'arch', 'Mach-O')} slice")
        return self.offset + relative

    def _payload(self, relative: int, length: int, what: str) -> int:
        absolute = self._range(relative, length, what)
        if length:
            if absolute < self.commands_end:
                raise MachOError(f"{what} overlaps the Mach-O header or load commands")
            self._payload_ranges.append((absolute, absolute + length, what))
        return absolute

    def _parse(self) -> None:
        self._range(0, self.header_size, "Mach-O header")
        if self._data[self.offset:self.offset + 4] != _MH_MAGIC_64:
            raise MachOError("Only little-endian 64-bit Mach-O slices are supported")
        (_, self.cputype, self.cpusubtype, self.filetype, self.ncmds,
         self.sizeofcmds, self.flags, _) = struct.unpack_from("<8I", self._data, self.offset)
        if self.cputype not in _CPU_ARCH:
            raise MachOError(f"Unsupported CPU type 0x{self.cputype:08x}")
        self.arch = _CPU_ARCH[self.cputype]
        self._range(self.header_size, self.sizeofcmds, "Load-command table")
        if self.ncmds > self.sizeofcmds // 8:
            raise MachOError("Load-command count exceeds the load-command table")
        cursor = self.offset + self.header_size
        for _ in range(self.ncmds):
            if cursor + 8 > self.commands_end:
                raise MachOError("Truncated load command")
            cmd, cmdsize = struct.unpack_from("<II", self._data, cursor)
            if cmdsize < 8 or cmdsize % 8 or cmdsize > self.commands_end - cursor:
                raise MachOError("Invalid or out-of-bounds 64-bit load-command size")
            command = LoadCommand(cmd, cursor, cmdsize)
            self.load_commands.append(command)
            self._parse_command(command)
            cursor += cmdsize
        if cursor != self.commands_end:
            raise MachOError("Load-command sizes do not match sizeofcmds")

        _check_disjoint([(s.addr, s.addr + s.size, s.name) for s in self.segments], "segment VM ranges")
        _check_disjoint([(s.offset, s.offset + s.file_size, s.name) for s in self.segments], "segment file ranges")
        _check_disjoint([(s.addr, s.addr + s.size, s.name) for s in self.sections], "section VM ranges")
        _check_disjoint([(s.offset, s.offset + s.size, s.name) for s in self.sections if s.file_backed], "section file ranges")
        self._parse_function_starts()

    def _parse_command(self, command: LoadCommand) -> None:
        cmd, cursor, cmdsize = command.cmd, command.offset, command.size
        if cmd == 0x19:  # LC_SEGMENT_64
            self._parse_segment(command)
        elif cmd == 0x1B:  # LC_UUID
            if cmdsize != 24 or self.uuid is not None:
                raise MachOError("Malformed or duplicate LC_UUID")
            self.uuid = str(uuid_module.UUID(bytes=self._data[cursor + 8:cursor + 24]))
        elif cmd in _LINKEDIT_DATA_COMMANDS:
            if cmdsize != 16:
                raise MachOError("Malformed linkedit data command")
            relative, size = struct.unpack_from("<II", self._data, cursor + 8)
            absolute = self._payload(relative, size, f"Load command 0x{cmd:x} data")
            if cmd == 0x26:
                if self._function_data is not None:
                    raise MachOError("Duplicate LC_FUNCTION_STARTS")
                self._function_data = (absolute, size)
        elif cmd == 0x2C:  # LC_ENCRYPTION_INFO_64
            if cmdsize != 24:
                raise MachOError("Malformed LC_ENCRYPTION_INFO_64")
            cryptoff, cryptsize, cryptid, _ = struct.unpack_from("<4I", self._data, cursor + 8)
            self._range(cryptoff, cryptsize, "Encrypted region")
            if cryptid:
                raise MachOError("Encrypted Mach-O slices are not supported (cryptid is nonzero)")
        elif cmd in _DYLIB_LOAD_COMMANDS or cmd == 0xD:  # LC_ID_DYLIB
            if cmdsize < 24:
                raise MachOError("Malformed dylib load command")
            nameoff = struct.unpack_from("<I", self._data, cursor + 8)[0]
            if nameoff < 24 or nameoff >= cmdsize:
                raise MachOError("Invalid dylib name offset")
            raw = self._data[cursor + nameoff:cursor + cmdsize]
            if b"\0" not in raw:
                raise MachOError("Unterminated dylib install name")
            try:
                name = raw.split(b"\0", 1)[0].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MachOError("Dylib install name is not UTF-8") from exc
            if not name:
                raise MachOError("Empty dylib install name")
            if cmd in _DYLIB_LOAD_COMMANDS:
                self.dylibs.append(name)
        elif cmd in {0x22, 0x80000022}:  # LC_DYLD_INFO[_ONLY]
            if cmdsize != 48:
                raise MachOError("Malformed LC_DYLD_INFO")
            for field in range(5):
                relative, size = struct.unpack_from("<II", self._data, cursor + 8 + field * 8)
                self._payload(relative, size, "Dyld info data")
        elif cmd == 0x2:  # LC_SYMTAB; nlist_64 is 16 bytes
            if cmdsize != 24:
                raise MachOError("Malformed LC_SYMTAB")
            symoff, nsyms, stroff, strsize = struct.unpack_from("<4I", self._data, cursor + 8)
            self._payload(symoff, nsyms * 16, "Symbol table")
            self._payload(stroff, strsize, "String table")
        elif cmd == 0xB:  # LC_DYSYMTAB
            if cmdsize != 80:
                raise MachOError("Malformed LC_DYSYMTAB")
            fields = struct.unpack_from("<18I", self._data, cursor + 8)
            for index, width, name in ((6, 8, "Table of contents"), (8, 56, "Module table"),
                                       (10, 4, "External references"), (12, 4, "Indirect symbols"),
                                       (14, 8, "External relocations"), (16, 8, "Local relocations")):
                self._payload(fields[index], fields[index + 1] * width, name)

    def _parse_segment(self, command: LoadCommand) -> None:
        if command.size < 72:
            raise MachOError("Truncated LC_SEGMENT_64")
        (rawname, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags) = struct.unpack_from(
            "<16sQQQQ4I", self._data, command.offset + 8)
        name = _name(rawname)
        if command.size != 72 + nsects * 80:
            raise MachOError("Segment section count does not match its command size")
        if vmsize > _UINT64_MAX - vmaddr or filesize > vmsize:
            raise MachOError("Invalid segment VM size")
        absolute = self._range(fileoff, filesize, f"Segment {name}")
        segment = Segment(name, vmaddr, vmsize, absolute, filesize, flags, maxprot, initprot)
        self.segments.append(segment)
        for index in range(nsects):
            (rawsect, rawseg, addr, size, sectoff, alignment, reloff, nreloc,
             section_flags, _, _, _) = struct.unpack_from("<16s16sQQ8I", self._data, command.offset + 72 + index * 80)
            sectname, segname = _name(rawsect), _name(rawseg)
            if segname != name:
                raise MachOError("Section segment name does not match its parent segment")
            if addr < vmaddr or addr > vmaddr + vmsize or size > vmaddr + vmsize - addr:
                raise MachOError(f"Section {sectname} extends outside its segment VM range")
            if alignment > 63:
                raise MachOError(f"Invalid section alignment for {sectname}")
            file_backed = (section_flags & 0xFF) not in _ZEROFILL_TYPES
            if file_backed:
                sectabsolute = self._payload(sectoff, size, f"Section {sectname}")
                if sectoff < fileoff or sectoff > fileoff + filesize or size > fileoff + filesize - sectoff:
                    raise MachOError(f"Section {sectname} extends outside its segment file range")
            else:
                # Zerofill offsets have no on-disk meaning, but still expose the
                # same absolute-offset convention as all other sections.
                sectabsolute = self._range(sectoff, 0, f"Zerofill section {sectname}")
            self._payload(reloff, nreloc * 8, f"Section {sectname} relocations")
            self.sections.append(Section(segname, sectname, addr, size, sectabsolute, section_flags, file_backed))

    def vm_to_offset(self, va: int, length: int = 1) -> int:
        """Map a complete file-backed VM range to an absolute file offset.

        Zerofill, unmapped memory, overflow, and ranges crossing backing-region
        boundaries raise MachOError rather than silently returning a bad offset.
        """
        if not isinstance(va, int) or not isinstance(length, int) or va < 0 or length < 1:
            raise MachOError("VM address must be nonnegative and length must be positive")
        if va > _UINT64_MAX or length - 1 > _UINT64_MAX - va:
            raise MachOError("VM range overflows 64 bits")
        for section in self.sections:
            if section.addr <= va < section.addr + section.size:
                if not section.file_backed or length > section.addr + section.size - va:
                    raise MachOError("VM range is zerofill or crosses a section boundary")
                return section.offset + va - section.addr
        for segment in self.segments:
            # SG_HIGHVM places file contents at the high end of a VM segment.
            backed_start = segment.addr + (segment.size - segment.file_size if segment.flags & 1 else 0)
            if backed_start <= va < backed_start + segment.file_size:
                if length > backed_start + segment.file_size - va:
                    raise MachOError("VM range crosses a file-backed segment boundary")
                # A range beginning in padding must not run through a zerofill
                # section or into a section with a different file mapping.
                for section in self.sections:
                    if va < section.addr + section.size and section.addr < va + length:
                        if not section.file_backed or section.offset - section.addr != segment.offset - backed_start:
                            raise MachOError("VM range crosses an incompatible section mapping")
                return segment.offset + va - backed_start
        raise MachOError(f"VM address 0x{va:x} has no file backing")

    def _parse_function_starts(self) -> None:
        if self._function_data is None or not self._function_data[1]:
            return
        text_segments = [segment for segment in self.segments if segment.name == "__TEXT"]
        if len(text_segments) != 1:
            raise MachOError("LC_FUNCTION_STARTS requires exactly one __TEXT segment")
        segment = text_segments[0]
        cursor, size = self._function_data
        end = cursor + size
        address = segment.addr
        while cursor < end:
            value = 0
            for index in range(10):
                if cursor >= end:
                    raise MachOError("Truncated function-start ULEB128")
                byte = self._data[cursor]
                cursor += 1
                if index == 9 and byte > 1:
                    raise MachOError("Function-start ULEB128 overflows 64 bits")
                value |= (byte & 0x7F) << (index * 7)
                if not byte & 0x80:
                    break
            else:
                raise MachOError("Overlong function-start ULEB128")
            if value == 0:
                if any(self._data[cursor:end]):
                    raise MachOError("Nonzero data after function-start terminator")
                return
            if value > _UINT64_MAX - address:
                raise MachOError("Function-start address overflows 64 bits")
            address += value
            if not segment.addr <= address < segment.addr + segment.size:
                raise MachOError("Function start lies outside __TEXT")
            absolute = self.vm_to_offset(address)
            if absolute < self.commands_end:
                raise MachOError("Function start points into the Mach-O header")
            self.function_starts.add(address)
        raise MachOError("Missing function-start terminator")


class MachO:
    """Parse a thin or big-endian FAT32/FAT64 universal Mach-O file."""

    def __init__(self, data: bytes):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("MachO data must be bytes-like")
        self.data = bytes(data)
        self.slices: list[Slice] = []
        magic = self.data[:4]
        if magic == _MH_MAGIC_64:
            self.slices.append(Slice(self.data, 0, len(self.data)))
        elif magic in {_FAT_MAGIC, _FAT_MAGIC_64}:
            self._parse_fat(magic == _FAT_MAGIC_64)
        else:
            raise MachOError("Unsupported or truncated Mach-O magic (requires 64-bit little-endian slices)")

    def _parse_fat(self, fat64: bool) -> None:
        if len(self.data) < 8:
            raise MachOError("Truncated FAT header")
        nfat = struct.unpack_from(">I", self.data, 4)[0]
        entry_size = 32 if fat64 else 20
        if not nfat or nfat > (len(self.data) - 8) // entry_size:
            raise MachOError("Invalid or truncated FAT architecture table")
        table_end = 8 + nfat * entry_size
        entries: list[tuple[int, int, int, int]] = []
        ranges: list[tuple[int, int, str]] = []
        for index in range(nfat):
            cursor = 8 + index * entry_size
            if fat64:
                cpu, subtype, offset, size, alignment, reserved = struct.unpack_from(">IIQQII", self.data, cursor)
                if reserved:
                    raise MachOError("Nonzero FAT64 reserved field")
            else:
                cpu, subtype, offset, size, alignment = struct.unpack_from(">5I", self.data, cursor)
            if cpu not in _CPU_ARCH:
                raise MachOError(f"Unsupported FAT CPU type 0x{cpu:08x}")
            if alignment > 63 or offset % (1 << alignment):
                raise MachOError("Invalid FAT slice alignment")
            if offset < table_end or size < 32 or offset > len(self.data) or size > len(self.data) - offset:
                raise MachOError("FAT slice overlaps its header or extends outside the file")
            entries.append((cpu, subtype, offset, size))
            ranges.append((offset, offset + size, f"slice {index}"))
        _check_disjoint(ranges, "FAT slices")
        for cpu, subtype, offset, size in entries:
            image = Slice(self.data, offset, size)
            if (image.cputype, image.cpusubtype) != (cpu, subtype):
                raise MachOError("FAT architecture does not match its Mach-O slice")
            self.slices.append(image)


def inject_dylib(data: bytes, install_name: str) -> bytes:
    """Add LC_LOAD_DYLIB to every slice, or return an already-injected image.

    All slices are validated before any edits. Existing bytes are preserved apart
    from each edited header's command count/size and its consumed zero padding.
    The caller must re-sign the edited image before use; signature commands stay.
    """
    if not isinstance(install_name, str) or not install_name or "\0" in install_name:
        raise MachOError("Dylib install name must be a nonempty string without NUL bytes")
    try:
        encoded_name = install_name.encode("utf-8") + b"\0"
    except UnicodeEncodeError as exc:
        raise MachOError("Dylib install name is not valid UTF-8") from exc
    command_size = (24 + len(encoded_name) + 7) & ~7
    if command_size > 0xFFFFFFFF:
        raise MachOError("Dylib install name is too long")
    command = struct.pack("<6I", 0xC, command_size, 24, 0, 0, 0) + encoded_name
    command += b"\0" * (command_size - len(command))
    macho = MachO(data)
    edits: list[Slice] = []
    for image in macho.slices:
        if install_name in image.dylibs:
            continue
        section_offsets = [s.offset for s in image.sections if s.file_backed and s.size]
        if not section_offsets:
            raise MachOError(f"Cannot establish safe header padding for {image.arch}: no file-backed sections")
        boundaries = section_offsets + [start for start, _, _ in image._payload_ranges]
        boundaries += [s.offset for s in image.segments if s.file_size and s.offset >= image.commands_end]
        limit = min(boundaries)
        if command_size > limit - image.commands_end:
            raise MachOError(f"Insufficient header padding for {image.arch}: need {command_size} bytes")
        if any(macho.data[image.commands_end:image.commands_end + command_size]):
            raise MachOError(f"Header padding for {image.arch} is not zero-filled")
        if image.ncmds == 0xFFFFFFFF or image.sizeofcmds > 0xFFFFFFFF - command_size:
            raise MachOError("Load-command header fields would overflow")
        edits.append(image)
    result = bytearray(macho.data)
    for image in edits:
        struct.pack_into("<II", result, image.offset + 16, image.ncmds + 1, image.sizeofcmds + command_size)
        result[image.commands_end:image.commands_end + command_size] = command
    return bytes(result)
