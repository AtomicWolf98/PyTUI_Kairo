import { describe, expect, it } from "vitest";
import { reduceRuntimeMessages } from "./stores";
import type { ChatMessageView, RuntimeEvent } from "./types";

function event(kind: string, payload: unknown): RuntimeEvent {
  return { kind, payload, timestamp: 1, sequence: 1 };
}

describe("runtime message reducer", () => {
  it("binds streaming deltas to the explicit message id", () => {
    let messages: ChatMessageView[] = [];
    messages = reduceRuntimeMessages(messages, event("message_started", {
      kind: "assistant",
      message_id: "m1",
      turn_id: "t1"
    }));
    messages = reduceRuntimeMessages(messages, event("content_delta", {
      message_id: "m1",
      delta: "hello"
    }));
    messages = reduceRuntimeMessages(messages, event("message_finished", {
      message_id: "m1"
    }));

    expect(messages).toHaveLength(1);
    expect(messages[0].id).toBe("m1");
    expect(messages[0].content).toBe("hello");
    expect(messages[0].streaming).toBe(false);
  });

  it("keeps same-name tool calls separated by tool_call_id", () => {
    let messages: ChatMessageView[] = [{
      id: "m1",
      role: "assistant",
      content: "",
      tools: []
    }];

    messages = reduceRuntimeMessages(messages, event("tool_started", {
      message_id: "m1",
      tool_call_id: "call-a",
      name: "read_file",
      arguments: "{\"path\":\"a.py\"}"
    }));
    messages = reduceRuntimeMessages(messages, event("tool_started", {
      message_id: "m1",
      tool_call_id: "call-b",
      name: "read_file",
      arguments: "{\"path\":\"b.py\"}"
    }));
    messages = reduceRuntimeMessages(messages, event("tool_finished", {
      message_id: "m1",
      tool_call_id: "call-a",
      name: "read_file",
      result: "A",
      success: true
    }));

    expect(messages[0].tools).toHaveLength(2);
    expect(messages[0].tools?.map(tool => tool.id)).toEqual(["call-a", "call-b"]);
    expect(messages[0].tools?.[0].result).toBe("A");
    expect(messages[0].tools?.[1].status).toBe("running");
  });
});
