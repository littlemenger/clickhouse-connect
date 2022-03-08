import struct
import uuid
import decimal
import pytz

from datetime import date, datetime, timezone, timedelta
from ipaddress import IPv4Address, IPv6Address
from binascii import hexlify

from clickhouse_connect.driver.rowbinary import string_leb128
from clickhouse_connect.datatypes.registry import ClickHouseType, TypeDef, get_from_name, type_map


class Int(ClickHouseType):
    __slots__ = 'size',
    signed = True

    def __init__(self, type_def: TypeDef):
        super().__init__(type_def)
        self.name_suffix = type_def.size
        self.size = type_def.size // 8

    def _from_row_binary(self, source: bytearray, loc: int):
        return int.from_bytes(source[loc:loc + self.size], 'little', signed=self.signed), loc + self.size

    def _to_row_binary(self, value: int, dest: bytearray):
        dest += value.to_bytes(self.size, 'little', signed=self.signed)


class UInt(Int):
    signed = False


class UInt64(ClickHouseType):
    signed = False

    def _from_row_binary(self, source: bytearray, loc: int):
        return int.from_bytes(source[loc:loc + 8], 'little', signed=self.signed), loc + 8

    def _to_row_binary(self, value: int, dest: bytearray):
        dest += value.to_bytes(8, 'little', signed=self.signed)


class Float32(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        return struct.unpack('f', source[loc:loc + 4])[0], loc + 4

    def _to_row_binary(self, value: float,  dest: bytearray,):
        dest += struct.pack('f', (value,))


class Float64(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        return struct.unpack('d', source[loc:loc + 8])[0], loc + 8

    def _to_row_binary(self, value: float, dest: bytearray) -> None:
        dest += struct.pack('d', (value,))


class DateTime(ClickHouseType):
    __slots__ = 'tzinfo',

    def __init__(self, type_def: TypeDef):
        super().__init__(type_def)
        if type_def.values:
            self.tzinfo = pytz.timezone(type_def.values[0][1:-1])
        else:
            self.tzinfo = timezone.utc

    def _from_row_binary(self, source: bytearray, loc: int):
        epoch = int.from_bytes(source[loc:loc + 4], 'little')
        return datetime.fromtimestamp(epoch, self.tzinfo), loc + 4

    def _to_row_binary(self, value:datetime, dest: bytearray) -> None:
        dest +=int(value.timestamp()).to_bytes(4, 'little', signed=True)


class Date(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        epoch_days = int.from_bytes(source[loc:loc + 2], 'little')
        return datetime.fromtimestamp(epoch_days * 86400, timezone.utc).date(), loc + 2

    def _to_row_binary(self, value: datetime, dest: bytearray):
        dest += (int(value.timestamp()) // 86400).to_bytes(2, 'little', signed=True)


class Date32(ClickHouseType):
    start_date = date(1970, 1, 1)

    def _from_row_binary(self, source, loc):
        days = int.from_bytes(source[loc:loc + 4], 'little', signed=True)
        return self.start_date + timedelta(days), loc + 4

    def _to_row_binary(self, value: date, dest: bytearray):
        dest += value - start


class DateTime64(ClickHouseType):
    __slots__ = 'prec', 'tzinfo'

    def __init__(self, type_def: TypeDef):
        super().__init__(type_def)
        self.name_suffix = type_def.arg_str
        self.prec = 10 ** type_def.values[0]
        if len(type_def.values) > 1:
            self.tzinfo = pytz.timezone(type_def.values[1][1:-1])
        else:
            self.tzinfo = timezone.utc

    def _from_row_binary(self, source, loc):
        ticks = int.from_bytes(source[loc:loc + 8], 'little', signed=True)
        seconds = ticks // self.prec
        dt_sec = datetime.fromtimestamp(seconds, self.tzinfo)
        microseconds = ((ticks - seconds * self.prec) * 1000000) // self.prec
        return dt_sec + timedelta(microseconds=microseconds), loc + 8


class String(ClickHouseType):
    _from_row_binary = staticmethod(string_leb128)


def _fixed_string_raw(value: bytearray):
    return value


def _fixed_string_decode(cls, value: bytearray):
    try:
        return value.decode(cls._encoding)
    except UnicodeDecodeError:
        return cls._encode_error(value)


def _hex_string(cls, value: bytearray):
    return hexlify(value).decode('utf8')


class FixedString(ClickHouseType):
    __slots__ = 'size',
    _encoding = 'utf8'
    _transform = staticmethod(_fixed_string_raw)
    _encode_error = staticmethod(_fixed_string_raw)

    def __init__(self, type_def: TypeDef):
        super().__init__(type_def)
        self.size = type_def.values[0]
        self.name_suffix = f'({self.size})'

    def _from_row_binary(self, source: bytearray, loc: int):
        return self._transform(source[loc:loc + self.size]), loc + self.size


def fixed_string_handling(method: str, encoding: str = 'utf8', encoding_error: str = 'hex'):
    if method == 'raw':
        FixedString._transform = staticmethod(_fixed_string_raw)
    elif method == 'decode':
        FixedString._encoding = encoding
        FixedString._transform = classmethod(_fixed_string_decode)
        if encoding_error == 'hex':
            FixedString._encode_error = classmethod(_hex_string)
        else:
            FixedString._encode_error = classmethod(lambda cls: '<binary data>')
    elif method == 'hex':
        FixedString._transform = staticmethod(_hex_string)


def uint64_handling(method: str):
    UInt64.signed = method.lower() == 'signed'


class UUID(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        int_high = int.from_bytes(source[loc:loc + 8], 'little')
        int_low = int.from_bytes(source[loc + 8:loc + 16], 'little')
        byte_value = int_high.to_bytes(8, 'big') + int_low.to_bytes(8, 'big')
        return uuid.UUID(bytes=byte_value), loc + 16


class Boolean(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        return source[loc] > 0, loc + 1


class Bool(Boolean):
    pass


class Decimal(ClickHouseType):
    __slots__ = 'size', 'prec'

    def __init__(self, type_def: TypeDef):
        super().__init__(type_def)
        size = type_def.size
        if size == 0:
            self.name_suffix = type_def.arg_str
            prec = type_def.values[0]
            self.prec = type_def.values[1]
            if prec < 1 or prec > 79:
                raise ArithmeticError(f"Invalid precision {prec} for ClickHouse Decimal type")
            if prec < 10:
                size = 32
            elif prec < 19:
                size = 64
            elif prec < 39:
                size = 128
            else:
                size = 256
        else:
            self.prec = type_def.values[0]
            self.name_suffix = f'{type_def.size}({self.prec})'
        self.size = size // 8

    def _from_row_binary(self, source, loc):
        neg = ''
        unscaled = int.from_bytes(source[loc:loc + self.size], 'little')
        if unscaled <= 0:
            neg = '-'
            unscaled = -unscaled
        digits = str(unscaled)
        return decimal.Decimal(f'{neg}{digits[:-self.prec]}.{digits[-self.prec:]}'), loc + self.size


class IPv4(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        return str(IPv4Address(int.from_bytes(source[loc:loc + 4], 'little'))), loc + 4


class IPv6(ClickHouseType):
    def _from_row_binary(self, source: bytearray, loc: int):
        end = loc + 16
        int_value = int.from_bytes(source[loc:end], 'big')
        if int_value & 0xFFFF00000000 == 0xFFFF00000000:
            return str(IPv4Address(int_value & 0xFFFFFFFF)), end
        return str(IPv6Address(int.from_bytes(source[loc:end], 'big'))), end
