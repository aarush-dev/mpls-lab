export interface AppState {
  cursor: number;
  playing: boolean;
  speed: number;
  loop: boolean;
}

export const initialAppState: AppState = {
  cursor: 0,
  playing: false,
  speed: 1,
  loop: false,
};

export type AppAction =
  | { type: 'TICK'; payload?: { deltaMs: number } }
  | { type: 'PLAY' }
  | { type: 'PAUSE' }
  | { type: 'SEEK'; payload: { cursor: number } }
  | { type: 'SET_SPEED'; payload: { speed: number } };

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'TICK':
      return { ...state, cursor: state.cursor + (action.payload?.deltaMs ?? 0) };
    case 'PLAY':
      return { ...state, playing: true };
    case 'PAUSE':
      return { ...state, playing: false };
    case 'SEEK':
      return { ...state, cursor: action.payload.cursor };
    case 'SET_SPEED':
      return { ...state, speed: action.payload.speed };
    default:
      return state;
  }
}
