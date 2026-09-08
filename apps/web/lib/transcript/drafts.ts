/** Drafts belong to the revision read and to one editor, never to a response. */
export interface WordEdit {
  baseRevision: number;
  draft: string | null;
  error: string | null;
  id: number;
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
  | { id: number; text: string; type: "save" }
  | { error: string | null; id: number; type: "settle" }
  | { id: number; original: string; revision: number; type: "review" };

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
    default:
      return state;
  }
}
