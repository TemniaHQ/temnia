/** Drafts belong to the revision read and to one editor, never to a response. */
export interface WordEdit {
  baseRevision: number;
  draft: string | null;
  error: string | null;
  id: number;
  identityId: string;
  index: number;
  original: string;
  pending: boolean;
}

export interface WordDrafts {
  activeId: number | null;
  drafts: WordEdit[];
}

type DraftEvent =
  | { edit: WordEdit; type: "open" }
  | { id: number; type: "activate" }
  | { text: string; type: "change" }
  | { type: "cancel" }
  | { type: "close" }
  | { type: "closeAfterStructure" }
  | { id: number; text: string; type: "save" }
  | { error: string | null; id: number; type: "settle" }
  | { id: number; original: string; revision: number; type: "review" }
  | {
      id: number;
      identityId: string;
      index: number;
      original: string;
      revision: number;
      type: "retarget";
    }
  | { identityIds: readonly string[]; type: "rebase" };

export const EMPTY_DRAFTS: WordDrafts = { activeId: null, drafts: [] };

export function wordDrafts(state: WordDrafts, event: DraftEvent): WordDrafts {
  switch (event.type) {
    case "open":
      return {
        activeId: event.edit.id,
        drafts: [...state.drafts, event.edit],
      };
    case "activate":
      return { ...state, activeId: event.id };
    case "close":
      return { ...state, activeId: null };
    case "closeAfterStructure": {
      const active = state.drafts.find((edit) => edit.id === state.activeId);
      const unchanged =
        active && active.draft === active.original && !active.pending;
      return {
        activeId: null,
        drafts: unchanged
          ? state.drafts.filter((edit) => edit.id !== active.id)
          : state.drafts,
      };
    }
    case "cancel":
      return {
        activeId: null,
        drafts: state.drafts.filter((edit) => edit.id !== state.activeId),
      };
    case "change":
      return {
        ...state,
        drafts: state.drafts.map((edit) =>
          edit.id === state.activeId && !edit.pending
            ? { ...edit, draft: event.text }
            : edit
        ),
      };
    case "save":
      return {
        ...state,
        drafts: state.drafts.map((edit) =>
          edit.id === event.id
            ? { ...edit, draft: event.text, error: null, pending: true }
            : edit
        ),
      };
    case "settle":
      if (event.error === null) {
        return {
          activeId: state.activeId === event.id ? null : state.activeId,
          drafts: state.drafts.filter((edit) => edit.id !== event.id),
        };
      }
      return {
        ...state,
        drafts: state.drafts.map((edit) =>
          edit.id === event.id
            ? { ...edit, error: event.error, pending: false }
            : edit
        ),
      };
    case "review":
      return {
        ...state,
        drafts: state.drafts.map((edit) =>
          edit.id === event.id && !edit.pending
            ? {
                ...edit,
                baseRevision: event.revision,
                error: null,
                original: event.original,
              }
            : edit
        ),
      };
    case "retarget":
      return {
        activeId: event.id,
        drafts: state.drafts.map((edit) =>
          edit.id === event.id && !edit.pending
            ? {
                ...edit,
                baseRevision: event.revision,
                error: null,
                identityId: event.identityId,
                index: event.index,
                original: event.original,
              }
            : edit
        ),
      };
    case "rebase":
      return {
        ...state,
        drafts: state.drafts.map((edit) => {
          const index = event.identityIds.indexOf(edit.identityId);
          return {
            ...edit,
            error:
              index < 0
                ? "That word was removed. Choose a new word to reapply this draft."
                : edit.error,
            index,
          };
        }),
      };
    default:
      return state;
  }
}
