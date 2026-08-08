"""Opaque identifiers used by the public kernel contract."""

from typing import NewType

KernelId = NewType("KernelId", str)
TurnId = NewType("TurnId", str)
SessionId = NewType("SessionId", str)
MessageId = NewType("MessageId", str)
ToolCallId = NewType("ToolCallId", str)
InteractionId = NewType("InteractionId", str)
EventId = NewType("EventId", str)
ProviderId = NewType("ProviderId", str)
ProfileId = NewType("ProfileId", str)
ResourceId = NewType("ResourceId", str)
MemoryId = NewType("MemoryId", str)
SecretId = NewType("SecretId", str)
TraceId = NewType("TraceId", str)
SpanId = NewType("SpanId", str)

