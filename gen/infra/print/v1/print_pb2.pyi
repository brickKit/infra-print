import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Channel(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CHANNEL_UNSPECIFIED: _ClassVar[Channel]
    CHANNEL_PDF: _ClassVar[Channel]
    CHANNEL_ZPL: _ClassVar[Channel]
CHANNEL_UNSPECIFIED: Channel
CHANNEL_PDF: Channel
CHANNEL_ZPL: Channel

class PrintTemplate(_message.Message):
    __slots__ = ("id", "name", "channel", "content", "version", "enabled", "created_at", "updated_at")
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    ENABLED_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    id: str
    name: str
    channel: Channel
    content: str
    version: int
    enabled: bool
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, id: _Optional[str] = ..., name: _Optional[str] = ..., channel: _Optional[_Union[Channel, str]] = ..., content: _Optional[str] = ..., version: _Optional[int] = ..., enabled: bool = ..., created_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class RenderRequest(_message.Message):
    __slots__ = ("template_id", "data_json", "source_component")
    TEMPLATE_ID_FIELD_NUMBER: _ClassVar[int]
    DATA_JSON_FIELD_NUMBER: _ClassVar[int]
    SOURCE_COMPONENT_FIELD_NUMBER: _ClassVar[int]
    template_id: str
    data_json: str
    source_component: str
    def __init__(self, template_id: _Optional[str] = ..., data_json: _Optional[str] = ..., source_component: _Optional[str] = ...) -> None: ...

class RenderResponse(_message.Message):
    __slots__ = ("content", "content_type")
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    CONTENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    content: bytes
    content_type: str
    def __init__(self, content: _Optional[bytes] = ..., content_type: _Optional[str] = ...) -> None: ...

class BatchGetTemplatesRequest(_message.Message):
    __slots__ = ("template_ids",)
    TEMPLATE_IDS_FIELD_NUMBER: _ClassVar[int]
    template_ids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, template_ids: _Optional[_Iterable[str]] = ...) -> None: ...

class BatchGetTemplatesResponse(_message.Message):
    __slots__ = ("templates",)
    TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    templates: _containers.RepeatedCompositeFieldContainer[PrintTemplate]
    def __init__(self, templates: _Optional[_Iterable[_Union[PrintTemplate, _Mapping]]] = ...) -> None: ...

class ListTemplatesRequest(_message.Message):
    __slots__ = ("channel", "cursor", "page_size")
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    PAGE_SIZE_FIELD_NUMBER: _ClassVar[int]
    channel: Channel
    cursor: str
    page_size: int
    def __init__(self, channel: _Optional[_Union[Channel, str]] = ..., cursor: _Optional[str] = ..., page_size: _Optional[int] = ...) -> None: ...

class ListTemplatesResponse(_message.Message):
    __slots__ = ("templates", "next_cursor")
    TEMPLATES_FIELD_NUMBER: _ClassVar[int]
    NEXT_CURSOR_FIELD_NUMBER: _ClassVar[int]
    templates: _containers.RepeatedCompositeFieldContainer[PrintTemplate]
    next_cursor: str
    def __init__(self, templates: _Optional[_Iterable[_Union[PrintTemplate, _Mapping]]] = ..., next_cursor: _Optional[str] = ...) -> None: ...
