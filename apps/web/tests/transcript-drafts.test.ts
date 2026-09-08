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
});
