import { describe, expect, it } from "vitest";
import { buildWorkspaceTree, filterWorkspaceTree } from "./WorkspacePage";

describe("workspace tree", () => {
  it("builds real directory nodes and sorts directories before files", () => {
    const tree = buildWorkspaceTree([
      "README.md",
      "agent/runtime.py",
      "agent/web/app.py",
      "agent/config.py"
    ]);

    expect(tree.map(node => [node.kind, node.name])).toEqual([
      ["directory", "agent"],
      ["file", "README.md"]
    ]);
    expect(tree[0].children.map(node => node.name)).toEqual(["web", "config.py", "runtime.py"]);
    expect(tree[0].children[0].children[0].path).toBe("agent/web/app.py");
  });

  it("keeps matching files and all their ancestors while filtering", () => {
    const tree = buildWorkspaceTree(["agent/runtime.py", "web/src/App.tsx", "web/src/api.ts"]);
    const filtered = filterWorkspaceTree(tree, "app");

    expect(filtered).toHaveLength(1);
    expect(filtered[0].path).toBe("web");
    expect(filtered[0].children[0].path).toBe("web/src");
    expect(filtered[0].children[0].children.map(node => node.path)).toEqual(["web/src/App.tsx"]);
  });
});
