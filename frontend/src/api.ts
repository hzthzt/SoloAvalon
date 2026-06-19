export type PlayerView = {
  id: string;
  seat_index: number;
  name: string;
  original_name: string | null;
  is_human: boolean;
  visible_role: string | null;
};

export type GameEvent = {
  id: number;
  game_id: string;
  event_index: number;
  event_type: string;
  public_payload: Record<string, unknown>;
  private_payload?: Record<string, unknown> | null;
  created_at: string;
};

export type GameState = {
  id: string;
  status: string;
  player_count: number;
  phase: string;
  current_round: number;
  leader_player_id: string;
  missions: Array<{
    round_number: number;
    team_size: number;
    fail_cards_required: number;
  }>;
  enabled_options: string[];
  human_player_id: string;
  human_role: string;
  human_faction: string;
  known_evil_player_ids: string[];
  lady_of_lake_holder_player_id: string | null;
  lady_of_lake_previous_holder_ids: string[];
  lady_of_lake_eligible_target_ids: string[];
  lady_of_lake_known_factions: Record<string, string>;
  players: PlayerView[];
  proposed_team: string[];
  speech_order: string[];
  speeches: Record<string, string>;
  votes_cast_count: number;
  quest_actions_submitted_count: number;
  quest_results: string[];
  failed_team_votes: number;
  forced_team: boolean;
  winner: string | null;
  assassination_target_id: string | null;
  next_human_action: string | null;
  events?: GameEvent[];
};

export type GameSummary = {
  id: string;
  status: string;
  player_count: number;
  current_round: number;
  current_phase: string;
  winner: string | null;
  created_at: string;
  updated_at: string;
};

export type LlmProfile = {
  id: string;
  name: string;
  base_url: string;
  api_key_masked: string;
  model: string;
  temperature: number;
  timeout: number;
  timeout_retries: number;
  created_at: string;
  updated_at: string;
};

export type LlmProfileInput = {
  id: string;
  name: string;
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  timeout: number;
  timeout_retries: number;
};

const jsonHeaders = { "Content-Type": "application/json" };

export async function createGame(payload: {
  player_count?: number;
  enabled_options?: string[];
  human_name?: string;
  ai_names?: string[];
  default_llm_profile_id?: string;
  ai_profile_overrides?: Record<string, string | null>;
}): Promise<GameState> {
  return request("/api/games", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

export async function getGame(gameId: string): Promise<GameState> {
  return request(`/api/games/${gameId}`);
}

export async function submitAction(
  gameId: string,
  payload: Record<string, unknown>
): Promise<GameState> {
  return request(`/api/games/${gameId}/actions`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

export async function submitHumanAiAction(gameId: string): Promise<GameState> {
  return request(`/api/games/${gameId}/ai-actions/human`, { method: "POST" });
}

export async function listGames(): Promise<GameSummary[]> {
  return request("/api/games");
}

export async function deleteGame(gameId: string): Promise<void> {
  await request(`/api/games/${gameId}`, { method: "DELETE" });
}

export async function exportGame(gameId: string, includePrivate = false): Promise<unknown> {
  const query = includePrivate ? "?include_private=true" : "";
  return request(`/api/games/${gameId}/export${query}`);
}

export async function listGameEvents(
  gameId: string,
  includePrivate = false
): Promise<GameEvent[]> {
  const query = includePrivate ? "?include_private=true" : "";
  return request(`/api/games/${gameId}/events${query}`);
}

export async function listProfiles(): Promise<LlmProfile[]> {
  return request("/api/llm-profiles");
}

export async function createProfile(payload: LlmProfileInput): Promise<LlmProfile> {
  return request("/api/llm-profiles", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

export async function updateProfile(
  profileId: string,
  payload: Omit<LlmProfileInput, "id">
): Promise<LlmProfile> {
  return request(`/api/llm-profiles/${profileId}`, {
    method: "PUT",
    headers: jsonHeaders,
    body: JSON.stringify(payload)
  });
}

export async function deleteProfile(profileId: string): Promise<void> {
  await request(`/api/llm-profiles/${profileId}`, { method: "DELETE" });
}

export async function testProfile(profileId: string): Promise<unknown> {
  return request(`/api/llm-profiles/${profileId}/test`, { method: "POST" });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const error = await response.json();
      message = error.detail ?? message;
    } catch {
      // Keep the HTTP status message.
    }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
