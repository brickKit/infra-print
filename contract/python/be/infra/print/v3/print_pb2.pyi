from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Format(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FORMAT_UNSPECIFIED: _ClassVar[Format]
    FORMAT_PDF: _ClassVar[Format]
    FORMAT_ZPL: _ClassVar[Format]
FORMAT_UNSPECIFIED: Format
FORMAT_PDF: Format
FORMAT_ZPL: Format

class RenderRequest(_message.Message):
    __slots__ = ("template", "version", "data", "format", "copy")
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    FORMAT_FIELD_NUMBER: _ClassVar[int]
    COPY_FIELD_NUMBER: _ClassVar[int]
    template: str
    version: int
    data: bytes
    format: Format
    copy: bool
    def __init__(self, template: _Optional[str] = ..., version: _Optional[int] = ..., data: _Optional[bytes] = ..., format: _Optional[_Union[Format, str]] = ..., copy: _Optional[bool] = ...) -> None: ...

class RenderResponse(_message.Message):
    __slots__ = ("content", "media_type", "template", "version", "version_digest", "content_digest")
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    MEDIA_TYPE_FIELD_NUMBER: _ClassVar[int]
    TEMPLATE_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    VERSION_DIGEST_FIELD_NUMBER: _ClassVar[int]
    CONTENT_DIGEST_FIELD_NUMBER: _ClassVar[int]
    content: bytes
    media_type: str
    template: str
    version: int
    version_digest: str
    content_digest: str
    def __init__(self, content: _Optional[bytes] = ..., media_type: _Optional[str] = ..., template: _Optional[str] = ..., version: _Optional[int] = ..., version_digest: _Optional[str] = ..., content_digest: _Optional[str] = ...) -> None: ...
