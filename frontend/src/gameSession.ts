import { gameEventsStreamUrl, newerGameState } from "./api.js";
import type { GameEvent, GameState } from "./api.js";

type StreamSource = {
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
};

export function subscribeGameEvents(
  gameId: string,
  after: number,
  onEvent: (event: GameEvent) => void,
  onState: (state: GameState) => void,
  sourceFactory: (url: string) => StreamSource = (url) => new EventSource(url)
) {
  const source = sourceFactory(gameEventsStreamUrl(gameId, after));
  source.addEventListener("game-event", (message) => {
    try { onEvent(JSON.parse((message as MessageEvent).data) as GameEvent); }
    catch { /* A later frame or reconnect can recover a malformed frame. */ }
  });
  source.addEventListener("game-state", (message) => {
    try { onState(JSON.parse((message as MessageEvent).data) as GameState); }
    catch { /* State frames do not advance the public event cursor. */ }
  });
  return () => source.close();
}

export async function waitForGameIdle(
  initial: GameState,
  read: (gameId: string) => Promise<GameState>,
  pause: () => Promise<void>,
  onState: (state: GameState) => Promise<GameState>,
  isCurrent: () => boolean
): Promise<GameState> {
  let current = initial;
  while (current.is_advancing && current.status === "active" && isCurrent()) {
    await pause();
    if (!isCurrent()) break;
    current = newerGameState(current, await read(initial.id));
    if (!isCurrent()) break;
    current = newerGameState(current, await onState(current));
  }
  return current;
}
