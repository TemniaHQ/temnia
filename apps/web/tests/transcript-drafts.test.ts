import { describe, expect, it } from "vitest";
import {
  EMPTY_DRAFTS,
  type WordEdit,
  wordDrafts,
} from "@/lib/transcript/drafts";

function draft(id: number): WordEdit {
  return {
    baseRevision: 1,
    draft: `correction ${id}`,
    error: null,
    id,
    identityId: `word-${id}`,
    index: id,
    original: `word ${id}`,
    pending: false,
  };
}

describe("word draft ownership", () => {
  it.each([
    null,
    "That edit cannot be saved",
    "Could not save",
    "Someone changed this transcript",
  ])(
    "settling save A (%s) preserves the active B draft and its revision",
    (error) => {
      let state = wordDrafts(EMPTY_DRAFTS, { edit: draft(1), type: "open" });
      state = wordDrafts(state, { id: 1, text: "saved A", type: "save" });
      state = wordDrafts(state, { edit: draft(2), type: "open" });
      state = wordDrafts(state, { text: "still typing B", type: "change" });
      const [, before] = state.drafts;
      state = wordDrafts(state, { error, id: 1, type: "settle" });
      expect(state.activeId).toBe(2);
      expect(state.drafts.find((edit) => edit.id === 2)).toEqual(before);
      if (error) {
        expect(state.drafts.find((edit) => edit.id === 1)).toMatchObject({
          baseRevision: 1,
          draft: "saved A",
          error,
          pending: false,
        });
      } else {
        expect(state.drafts).toHaveLength(1);
      }
    }
  );

  it("retains a closed draft and only changes its base after explicit review", () => {
    let state = wordDrafts(EMPTY_DRAFTS, { edit: draft(1), type: "open" });
    state = wordDrafts(state, { type: "close" });
    state = wordDrafts(state, { id: 1, type: "activate" });
    expect(state.drafts[0]).toEqual(draft(1));
    state = wordDrafts(state, {
      id: 1,
      original: "new current word",
      revision: 4,
      type: "review",
    });
    expect(state.drafts[0]).toMatchObject({
      baseRevision: 4,
      draft: "correction 1",
      original: "new current word",
    });
  });

  it("tracks the stable identity after a splice and retains a deleted target", () => {
    let moved = wordDrafts(EMPTY_DRAFTS, { edit: draft(1), type: "open" });
    moved = wordDrafts(moved, {
      identityIds: ["new", "word-1"],
      type: "rebase",
    });
    expect(moved.drafts[0]).toMatchObject({ identityId: "word-1", index: 1 });
    moved = wordDrafts(moved, { identityIds: ["new"], type: "rebase" });
    expect(moved.drafts[0]).toMatchObject({
      error: "That word was removed. Choose a new word to reapply this draft.",
      identityId: "word-1",
      index: -1,
    });
    moved = wordDrafts(moved, {
      id: 1,
      identityId: "replacement",
      index: 0,
      original: "replacement word",
      revision: 3,
      type: "retarget",
    });
    expect(moved.drafts[0]).toMatchObject({
      baseRevision: 3,
      draft: "correction 1",
      error: null,
      identityId: "replacement",
      index: 0,
      original: "replacement word",
    });
  });

  it("drops an unchanged selection after structure but retains typed text", () => {
    const unchanged = { ...draft(1), draft: "word 1" };
    let selected = wordDrafts(EMPTY_DRAFTS, {
      edit: unchanged,
      type: "open",
    });
    selected = wordDrafts(selected, { type: "closeAfterStructure" });
    expect(selected).toEqual(EMPTY_DRAFTS);

    let typed = wordDrafts(EMPTY_DRAFTS, {
      edit: unchanged,
      type: "open",
    });
    typed = wordDrafts(typed, { text: "typed correction", type: "change" });
    typed = wordDrafts(typed, { type: "closeAfterStructure" });
    expect(typed.activeId).toBeNull();
    expect(typed.drafts).toHaveLength(1);
    expect(typed.drafts[0]?.draft).toBe("typed correction");
  });
});
