import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Bot,
  Check,
  Download,
  History,
  MessageSquare,
  Pencil,
  Play,
  RefreshCw,
  Save,
  Settings,
  Shield,
  Swords,
  Trash2,
  Users,
  X
} from "lucide-react";
import {
  archiveGame,
  ApiError,
  createGame,
  createProfile,
  deleteGame,
  deleteProfile,
  exportGame,
  AiDecisionDetail,
  GameEvent,
  GameState,
  gameEventsStreamUrl,
  GameSummary,
  getRoomDetail,
  getGame,
  listGameEvents,
  listGames,
  listProfiles,
  LlmProfile,
  LlmProfileInput,
  PlayerView,
  retryPausedGame,
  RoomDetail,
  submitAction,
  submitHumanAiAction,
  testProfile,
  updateProfile
} from "./api";
import {
  buildFlowReview,
  currentActivityText,
  eventsForFlowReview,
  feedItemsForDisplay,
  mergeEventsForFlowReview
} from "./flowReview";
import type { FlowReview, InformationFeedItem, ReviewRound, SummaryCard } from "./flowReview";

type Tab = "game" | "profiles" | "rooms";

type AiDecisionPair = {
  event: GameEvent;
  decision?: AiDecisionDetail;
};

const playerCountOptions = [5, 6, 7, 8, 9, 10];

const emptyProfile: LlmProfileInput = {
  id: "",
  name: "",
  base_url: "https://api.example.com/v1",
  api_key: "",
  model: "",
  temperature: 0.7,
  timeout: 30,
  timeout_retries: 5
};

export function App() {
  const [tab, setTab] = useState<Tab>("game");
  const [game, setGame] = useState<GameState | null>(null);
  const [profiles, setProfiles] = useState<LlmProfile[]>([]);
  const [games, setGames] = useState<GameSummary[]>([]);
  const [playerCount, setPlayerCount] = useState(5);
  const [ladyOfLakeEnabled, setLadyOfLakeEnabled] = useState(false);
  const [tristanIsoldeEnabled, setTristanIsoldeEnabled] = useState(false);
  const [roleTipDetailEnabled, setRoleTipDetailEnabled] = useState(false);
  const [humanName, setHumanName] = useState("真人玩家");
  const [aiNames, setAiNames] = useState(["AI 1", "AI 2", "AI 3", "AI 4"]);
  const [defaultProfileId, setDefaultProfileId] = useState("");
  const [aiProfileOverrides, setAiProfileOverrides] = useState(["", "", "", ""]);
  const [autoPlayHuman, setAutoPlayHuman] = useState(false);
  const [selectedTeam, setSelectedTeam] = useState<string[]>([]);
  const [activeGameEvents, setActiveGameEvents] = useState<GameEvent[]>([]);
  const [speech, setSpeech] = useState("");
  const [assassinationTarget, setAssassinationTarget] = useState("");
  const [ladyOfLakeTarget, setLadyOfLakeTarget] = useState("");
  const [profileForm, setProfileForm] = useState<LlmProfileInput>(emptyProfile);
  const [profileTestResults, setProfileTestResults] = useState<Record<string, string>>({});
  const [editingProfileId, setEditingProfileId] = useState("");
  const [selectedRoomGameId, setSelectedRoomGameId] = useState("");
  const [selectedLogPlayerNames, setSelectedLogPlayerNames] = useState<Record<string, string>>({});
  const [eventLog, setEventLog] = useState<GameEvent[]>([]);
  const [roomDetail, setRoomDetail] = useState<RoomDetail | null>(null);
  const [activeRoomDetail, setActiveRoomDetail] = useState<RoomDetail | null>(null);
  const [exportedLog, setExportedLog] = useState("");
  const [notice, setNotice] = useState("");
  const [manualRetryRepeat, setManualRetryRepeat] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [autoPlaying, setAutoPlaying] = useState(false);
  const [startingGame, setStartingGame] = useState(false);

  const enabledOptions = [
    ...(ladyOfLakeEnabled ? ["lady_of_lake"] : []),
    ...(tristanIsoldeEnabled ? ["tristan_isolde"] : []),
    ...(roleTipDetailEnabled ? ["role_tip_detail"] : [])
  ];
  const requiredTeamSize = game ? game.missions[game.current_round - 1]?.team_size ?? 2 : 2;
  const humanOnQuest = Boolean(game?.proposed_team.includes(game.human_player_id));
  const canSaveProfile = Boolean(
    profileForm.id.trim() &&
      profileForm.name.trim() &&
      profileForm.base_url.trim() &&
      profileForm.model.trim() &&
      Number.isFinite(profileForm.timeout_retries) &&
      profileForm.timeout_retries >= 0 &&
      (editingProfileId || profileForm.api_key.trim())
  );
  const selectedLogReview = useMemo(() => {
    const names = new Map(Object.entries(selectedLogPlayerNames));
    return buildFlowReview(eventLog, names);
  }, [eventLog, selectedLogPlayerNames]);
  const activePlayerNames = useMemo(
    () => new Map(game?.players.map((player) => [player.id, player.name]) ?? []),
    [game?.players]
  );
  const roomDetailForActiveReview =
    game && isGameComplete(game) && activeRoomDetail?.game.id === game.id ? activeRoomDetail : null;
  const activeReviewEvents = roomDetailForActiveReview?.events ?? activeGameEvents;
  const activeGameReview = useMemo(
    () => buildFlowReview(activeReviewEvents, activePlayerNames),
    [activeReviewEvents, activePlayerNames]
  );
  const playableRooms = useMemo(
    () => games.filter((summary) => summary.archived_at === null),
    [games]
  );
  const archivedRooms = useMemo(
    () => games.filter((summary) => summary.archived_at !== null),
    [games]
  );
  const activeTeamAttemptNumber = game ? currentTeamAttemptNumber(game, activeGameReview) : 1;
  const activeGameId = game?.id ?? "";

  useEffect(() => {
    void refreshLists();
  }, []);

  useEffect(() => {
    if (!activeGameId) {
      return;
    }
    return subscribeGameEvents(activeGameId, latestEventIndex(activeGameEvents), (event) => {
      setActiveGameEvents((current) => mergeEventsForFlowReview(current, [event]));
    });
  }, [activeGameId]);

  useEffect(() => {
    const aiCount = playerCount - 1;
    setAiNames((current) =>
      Array.from({ length: aiCount }, (_, index) => current[index] ?? `AI ${index + 1}`)
    );
    setAiProfileOverrides((current) =>
      Array.from({ length: aiCount }, (_, index) => current[index] ?? "")
    );
    if (playerCount < 8) {
      setLadyOfLakeEnabled(false);
    }
    if (playerCount < 9) {
      setTristanIsoldeEnabled(false);
    }
  }, [playerCount]);

  useEffect(() => {
    if (!game) {
      return;
    }
    const current = selectedTeam.filter((playerId) =>
      game.players.some((player) => player.id === playerId)
    );
    if (current.length === 0) {
      setSelectedTeam([game.human_player_id]);
    }
  }, [game?.id, game?.current_round]);

  async function refreshLists() {
    await run(async () => {
      const [profileList, gameList] = await Promise.all([listProfiles(), listGames()]);
      setProfiles(profileList);
      setGames(gameList);
      setDefaultProfileId((current) =>
        profileList.some((profile) => profile.id === current) ? current : profileList[0]?.id ?? ""
      );
      setAiProfileOverrides((current) =>
        current.map((profileId) =>
          profileId && !profileList.some((profile) => profile.id === profileId) ? "" : profileId
        )
      );
    });
  }

  async function applyActiveGame(updated: GameState, resetEvents = false) {
    const stateEvents = updated.events ?? [];
    setGame(updated);
    setActiveGameEvents((current) =>
      resetEvents ? stateEvents : eventsForFlowReview(stateEvents, current)
    );
    try {
      const fetchedEvents = await listGameEvents(updated.id);
      const freshestEvents = eventsForFlowReview(stateEvents, fetchedEvents);
      setActiveGameEvents((current) =>
        resetEvents ? freshestEvents : eventsForFlowReview(freshestEvents, current)
      );
    } catch {
      setActiveGameEvents((current) =>
        resetEvents ? stateEvents : eventsForFlowReview(stateEvents, current)
      );
    }
    await refreshActiveRoomDetailIfComplete(updated);
  }

  async function refreshActiveRoomDetailIfComplete(updated: GameState) {
    if (!isGameComplete(updated)) {
      setActiveRoomDetail(null);
      return;
    }
    const detail = await getRoomDetail(updated.id);
    setActiveRoomDetail(detail);
    setActiveGameEvents((current) => eventsForFlowReview(detail.events, current));
  }

  async function startGame() {
    await run(async () => {
      setStartingGame(true);
      setGame(null);
      setActiveGameEvents([]);
      setActiveRoomDetail(null);
      let created = await createGame({
        player_count: playerCount,
        enabled_options: enabledOptions,
        human_name: humanName,
        ai_names: aiNames,
        default_llm_profile_id: defaultProfileId || undefined,
        ai_profile_overrides: Object.fromEntries(
          aiProfileOverrides.map((profileId, index) => [
            `player_${index + 2}`,
            profileId || null
          ])
        )
      });
      await applyActiveGame(created, true);
      if (autoPlayHuman) {
        created = await driveHumanWithAi(created, true);
      }
      setSelectedTeam([created.human_player_id]);
      setSelectedRoomGameId(created.id);
      setSpeech("");
      setAssassinationTarget("");
      setLadyOfLakeTarget("");
      setTab("game");
      await refreshLists();
      setStartingGame(false);
    }, () => {
      setStartingGame(false);
    });
  }

  async function refreshGame() {
    if (!game) {
      return;
    }
    await run(async () => {
      const updated = await getGame(game.id);
      await applyActiveGame(updated);
    });
  }

  async function sendAction(payload: Record<string, unknown>) {
    if (!game) {
      return;
    }
    await run(async () => {
      const updated = await submitAction(game.id, payload);
      await applyActiveGame(updated);
      if (payload.action_type === "speak") {
        setSpeech("");
      }
      await refreshLists();
    });
  }

  async function sendHumanAiAction(repeat: boolean) {
    if (!game) {
      return;
    }
    await run(async () => {
      const updated = await driveHumanWithAi(game, repeat);
      await applyActiveGame(updated);
      setSelectedTeam([updated.human_player_id]);
      setSpeech("");
      setAssassinationTarget("");
      setLadyOfLakeTarget("");
      setManualRetryRepeat(null);
      await refreshLists();
    }, (error) => {
      if (isRetryableAiDecisionError(error)) {
        setManualRetryRepeat(repeat);
      }
    });
  }

  async function sendPausedGameRetry() {
    if (!game) {
      return;
    }
    await run(async () => {
      const updated = await retryPausedGame(game.id);
      await applyActiveGame(updated);
      setSelectedTeam([updated.human_player_id]);
      setSpeech("");
      setAssassinationTarget("");
      setLadyOfLakeTarget("");
      await refreshLists();
    });
  }

  async function driveHumanWithAi(start: GameState, repeat: boolean) {
    setAutoPlaying(true);
    try {
      let current = start;
      const maxSteps = repeat ? 80 : 1;
      for (let step = 0; step < maxSteps; step += 1) {
        if (!current.next_human_action || current.phase === "complete") {
          return current;
        }
        current = await submitHumanAiAction(current.id);
        await applyActiveGame(current);
        setSelectedTeam([current.human_player_id]);
        if (repeat) {
          await pauseForFlow();
        }
      }
      if (current.next_human_action && current.phase !== "complete") {
        setNotice("AI 接管已暂停：达到安全步数");
      }
      return current;
    } finally {
      setAutoPlaying(false);
    }
  }

  async function saveProfile() {
    await run(async () => {
      if (editingProfileId) {
        const { id: _id, ...payload } = profileForm;
        await updateProfile(editingProfileId, payload);
      } else {
        await createProfile(profileForm);
      }
      resetProfileForm();
      await refreshLists();
    });
  }

  async function removeProfile(profileId: string) {
    await run(async () => {
      await deleteProfile(profileId);
      if (editingProfileId === profileId) {
        resetProfileForm();
      }
      await refreshLists();
    });
  }

  function editProfile(profile: LlmProfile) {
    setEditingProfileId(profile.id);
    setProfileForm({
      id: profile.id,
      name: profile.name,
      base_url: profile.base_url,
      api_key: "",
      model: profile.model,
      temperature: profile.temperature,
      timeout: profile.timeout,
      timeout_retries: profile.timeout_retries
    });
  }

  function resetProfileForm() {
    setEditingProfileId("");
    setProfileForm({ ...emptyProfile });
  }

  async function checkProfile(profileId: string) {
    await run(async () => {
      const result = await testProfile(profileId);
      const resultText = JSON.stringify(result);
      setProfileTestResults((current) => ({ ...current, [profileId]: resultText }));
      setNotice(resultText);
    });
  }

  async function enterPlayableRoom(gameId: string) {
    await run(async () => {
      const [updated, events] = await Promise.all([getGame(gameId), listGameEvents(gameId)]);
      setSelectedRoomGameId(gameId);
      await applyActiveGame({ ...updated, events }, true);
      setSelectedTeam([updated.human_player_id]);
      setSpeech("");
      setAssassinationTarget("");
      setLadyOfLakeTarget("");
      setTab("game");
    });
  }

  async function leaveActiveRoom() {
    await run(async () => {
      setGame(null);
      setActiveGameEvents([]);
      setActiveRoomDetail(null);
      setSpeech("");
      setAssassinationTarget("");
      setLadyOfLakeTarget("");
      await refreshLists();
    });
  }

  async function archiveRoom(gameId: string) {
    await run(async () => {
      await archiveGame(gameId);
      if (game?.id === gameId) {
        setGame(null);
        setActiveGameEvents([]);
        setActiveRoomDetail(null);
      }
      if (selectedRoomGameId === gameId) {
        setSelectedRoomGameId("");
      }
      await refreshLists();
    });
  }

  async function removeGame(gameId: string) {
    await run(async () => {
      await deleteGame(gameId);
      if (game?.id === gameId) {
        setGame(null);
        setActiveGameEvents([]);
        setActiveRoomDetail(null);
      }
      if (selectedRoomGameId === gameId) {
        setSelectedRoomGameId("");
        setSelectedLogPlayerNames({});
        setEventLog([]);
        setRoomDetail(null);
        setExportedLog("");
      }
      await refreshLists();
    });
  }

  async function showExport(gameId: string) {
    await run(async () => {
      const payload = await exportGame(gameId, true);
      setSelectedRoomGameId(gameId);
      setExportedLog(JSON.stringify(payload, null, 2));
    });
  }

  async function showEvents(gameId: string) {
    await run(async () => {
      const detail = await getRoomDetail(gameId);
      setSelectedRoomGameId(gameId);
      setSelectedLogPlayerNames(
        Object.fromEntries(detail.game.players.map((player) => [player.id, player.name]))
      );
      setEventLog(detail.events);
      setRoomDetail(detail);
    });
  }

  async function run(work: () => Promise<void>, onError?: (error: unknown) => void) {
    setBusy(true);
    setNotice("");
    setManualRetryRepeat(null);
    try {
      await work();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
      onError?.(error);
      if (isRetryableAiDecisionError(error) && game) {
        try {
          const updated = await getGame(game.id);
          await applyActiveGame(updated);
          await refreshLists();
        } catch {
          // 保留原始错误提示，刷新失败不覆盖它。
        }
      }
    } finally {
      setBusy(false);
    }
  }

  const activeAction = game?.next_human_action;
  const showSetupState = tab === "game" && !game && !startingGame;
  const showStartingState = tab === "game" && startingGame;
  const showPlayState = tab === "game" && Boolean(game);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>SoloAvalon</h1>
          <p>
            {game
              ? `${game.display_name} · ${game.player_count} 人 · ${phaseLabel(game.phase)}`
              : `Local ${playerCount}-player Avalon`}
          </p>
        </div>
        <nav className="tabs" aria-label="Main views">
          <button className={tab === "game" ? "active" : ""} onClick={() => setTab("game")}>
            <Swords size={18} /> 对局
          </button>
          <button
            className={tab === "profiles" ? "active" : ""}
            onClick={() => setTab("profiles")}
          >
            <Settings size={18} /> 模型
          </button>
          <button className={tab === "rooms" ? "active" : ""} onClick={() => setTab("rooms")}>
            <History size={18} /> 房间
          </button>
        </nav>
      </header>

      {notice && (
        <div className="notice">
          <span>{notice}</span>
          {manualRetryRepeat !== null && game?.next_human_action && (
            <button
              type="button"
              onClick={() => sendHumanAiAction(manualRetryRepeat)}
              disabled={busy || autoPlaying}
            >
              <RefreshCw size={16} /> 手动重试
            </button>
          )}
        </div>
      )}

      {showSetupState && (
        <section className="game-layout setup-layout">
          <section className="panel setup-panel">
            <div className="section-title">
              <Play size={18} />
              <h2>开局</h2>
            </div>
            <label>
              默认模型
              <select
                value={defaultProfileId}
                onChange={(event) => setDefaultProfileId(event.target.value)}
              >
                <option value="" disabled>
                  请选择模型
                </option>
                {profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} / {profile.model}
                  </option>
                ))}
              </select>
            </label>
            <label>
              玩家人数
              <select
                value={playerCount}
                onChange={(event) => setPlayerCount(Number(event.target.value))}
              >
                {playerCountOptions.map((count) => (
                  <option key={count} value={count}>
                    {count} 人
                  </option>
                ))}
              </select>
            </label>
            <label>
              真人昵称
              <input value={humanName} onChange={(event) => setHumanName(event.target.value)} />
            </label>
            <div className="ai-name-grid">
              {aiNames.map((name, index) => (
                <div className="ai-config" key={index}>
                  <label>
                    AI {index + 1}
                    <input
                      value={name}
                      onChange={(event) => {
                        const next = [...aiNames];
                        next[index] = event.target.value;
                        setAiNames(next);
                      }}
                    />
                  </label>
                  <label>
                    覆盖模型
                    <select
                      value={aiProfileOverrides[index]}
                      onChange={(event) => {
                        const next = [...aiProfileOverrides];
                        next[index] = event.target.value;
                        setAiProfileOverrides(next);
                      }}
                    >
                      <option value="">默认</option>
                      {profiles.map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.name}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
              ))}
            </div>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={autoPlayHuman}
                onChange={(event) => setAutoPlayHuman(event.target.checked)}
              />
              <span>AI 接管真人</span>
            </label>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={ladyOfLakeEnabled}
                disabled={playerCount < 8}
                onChange={(event) => setLadyOfLakeEnabled(event.target.checked)}
              />
              <span>湖中仙女</span>
            </label>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={tristanIsoldeEnabled}
                disabled={playerCount < 9}
                onChange={(event) => setTristanIsoldeEnabled(event.target.checked)}
              />
              <span>崔斯坦 / 伊索尔德</span>
            </label>
            <label className="switch-row">
              <input
                type="checkbox"
                checked={roleTipDetailEnabled}
                onChange={(event) => setRoleTipDetailEnabled(event.target.checked)}
              />
              <span>进阶模式</span>
            </label>
            <button className="primary" onClick={startGame} disabled={busy || !defaultProfileId}>
              <Play size={18} /> {autoPlayHuman ? "新对局并代打" : "新对局"}
            </button>
          </section>

          <PlayableRoomList
            rooms={playableRooms}
            enterPlayableRoom={enterPlayableRoom}
            archiveRoom={archiveRoom}
          />
        </section>
      )}

      {showPlayState && game && (
        <section className="play-focus-layout">
              <GameDesk
                game={game}
                requiredTeamSize={requiredTeamSize}
                review={activeGameReview}
                roomDetail={roomDetailForActiveReview}
                teamAttemptNumber={activeTeamAttemptNumber}
                onLeaveRoom={leaveActiveRoom}
              />
              <section className="panel action-panel">
                <div className="section-title">
                  <Shield size={18} />
                  <h2>行动</h2>
                  <button className="icon-button" onClick={refreshGame} title="刷新">
                    <RefreshCw size={18} />
                  </button>
                </div>
                <StatusStrip game={game} />
                {Object.keys(game.lady_of_lake_known_factions).length > 0 && (
                  <div className="lake-results">
                    {Object.entries(game.lady_of_lake_known_factions).map(([playerId, faction]) => (
                      <span key={playerId}>
                        湖女：{playerName(game, playerId)} 是{factionLabel(faction)}
                      </span>
                    ))}
                  </div>
                )}
                {game.status === "error_paused" && (
                  <div className="button-row">
                    <button
                      className="primary"
                      disabled={busy || autoPlaying}
                      onClick={sendPausedGameRetry}
                    >
                      <RefreshCw size={18} /> 重试推进
                    </button>
                  </div>
                )}
                {activeAction && (
                  <div className="button-row">
                    <button disabled={busy || autoPlaying} onClick={() => sendHumanAiAction(false)}>
                      <Bot size={18} /> AI 代打一步
                    </button>
                    <button
                      className="primary"
                      disabled={busy || autoPlaying}
                      onClick={() => sendHumanAiAction(true)}
                    >
                      <Bot size={18} /> 连续代打
                    </button>
                  </div>
                )}
                {activeAction === "propose_team" && (
                  <div className="action-block">
                    <div className="mini-heading">队伍 {selectedTeam.length}/{requiredTeamSize}</div>
                    <div className="check-grid">
                      {game.players.map((player) => {
                        const checked = selectedTeam.includes(player.id);
                        const disabled = !checked && selectedTeam.length >= requiredTeamSize;
                        return (
                          <label key={player.id} className={checked ? "checked" : ""}>
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={disabled}
                              onChange={() => {
                                setSelectedTeam((current) =>
                                  checked
                                    ? current.filter((id) => id !== player.id)
                                    : [...current, player.id]
                                );
                              }}
                            />
                            {player.name}
                          </label>
                        );
                      })}
                    </div>
                    <button
                      className="primary"
                      disabled={selectedTeam.length !== requiredTeamSize || busy}
                      onClick={() =>
                        sendAction({ action_type: "propose_team", team: selectedTeam })
                      }
                    >
                      <Users size={18} /> 提交队伍
                    </button>
                  </div>
                )}

                {activeAction === "speak" && (
                  <div className="action-block">
                    <textarea
                      value={speech}
                      onChange={(event) => setSpeech(event.target.value)}
                      rows={5}
                    />
                    <button
                      className="primary"
                      disabled={!speech.trim() || busy}
                      onClick={() => sendAction({ action_type: "speak", message: speech })}
                    >
                      <MessageSquare size={18} /> 发言
                    </button>
                  </div>
                )}

                {activeAction === "vote" && (
                  <div className="button-row">
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() => sendAction({ action_type: "vote", vote: "approve" })}
                    >
                      <Check size={18} /> 赞成
                    </button>
                    <button
                      className="danger"
                      disabled={busy}
                      onClick={() => sendAction({ action_type: "vote", vote: "reject" })}
                    >
                      <X size={18} /> 反对
                    </button>
                  </div>
                )}

                {activeAction === "mission_action" && humanOnQuest && (
                  <div className="button-row">
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() =>
                        sendAction({ action_type: "mission_action", mission_action: "success" })
                      }
                    >
                      <Check size={18} /> 成功
                    </button>
                    {game.human_faction === "evil" && (
                      <button
                        className="danger"
                        disabled={busy}
                        onClick={() =>
                          sendAction({ action_type: "mission_action", mission_action: "fail" })
                        }
                      >
                        <X size={18} /> 失败
                      </button>
                    )}
                  </div>
                )}

                {activeAction === "assassinate" && (
                  <div className="action-block">
                    <select
                      value={assassinationTarget}
                      onChange={(event) => setAssassinationTarget(event.target.value)}
                    >
                      <option value="">选择目标</option>
                      {game.players
                        .filter((player) => player.id !== game.human_player_id)
                        .map((player) => (
                          <option key={player.id} value={player.id}>
                            {player.name}
                          </option>
                        ))}
                    </select>
                    <button
                      className="danger"
                      disabled={!assassinationTarget || busy}
                      onClick={() =>
                        sendAction({
                          action_type: "assassinate",
                          target_player_id: assassinationTarget
                        })
                      }
                    >
                      <Swords size={18} /> 刺杀
                    </button>
                  </div>
                )}

                {activeAction === "use_lady_of_lake" && (
                  <div className="action-block">
                    <select
                      value={ladyOfLakeTarget}
                      onChange={(event) => setLadyOfLakeTarget(event.target.value)}
                    >
                      <option value="">选择湖女目标</option>
                      {game.lady_of_lake_eligible_target_ids.map((playerId) => (
                        <option key={playerId} value={playerId}>
                          {playerName(game, playerId)}
                        </option>
                      ))}
                    </select>
                    <button
                      className="primary"
                      disabled={!ladyOfLakeTarget || busy}
                      onClick={() =>
                        sendAction({
                          action_type: "use_lady_of_lake",
                          target_player_id: ladyOfLakeTarget
                        })
                      }
                    >
                      <Shield size={18} /> 使用湖女
                    </button>
                  </div>
                )}

                {!activeAction && <div className="empty-state">等待 AI 或对局已结算</div>}
                <ActionRoundSummary summaries={activeGameReview.summaries} />
              </section>
        </section>
      )}

      {showStartingState && (
        <section className="play-focus-layout">
          <StartingGameDesk playerCount={playerCount} />
          <StartingActionPanel />
        </section>
      )}

      {tab === "profiles" && (
        <section className="two-column">
          <section className="panel">
            <div className="section-title">
              <Save size={18} />
              <h2>{editingProfileId ? "编辑模型" : "模型配置"}</h2>
            </div>
            <ProfileForm
              form={profileForm}
              setForm={setProfileForm}
              isEditing={Boolean(editingProfileId)}
            />
            <div className="button-row">
              <button className="primary" onClick={saveProfile} disabled={busy || !canSaveProfile}>
                <Save size={18} /> 保存
              </button>
              {editingProfileId && (
                <button onClick={resetProfileForm} disabled={busy}>
                  <X size={18} /> 取消
                </button>
              )}
            </div>
          </section>
          <section className="list-panel">
            {profiles.map((profile) => (
              <article className="profile-item" key={profile.id}>
                <div>
                  <h3>{profile.name}</h3>
                  <dl className="profile-runtime-fields">
                    <div>
                      <dt>ID</dt>
                      <dd>{profile.id}</dd>
                    </div>
                    <div>
                      <dt>Base URL</dt>
                      <dd>{profile.base_url}</dd>
                    </div>
                    <div>
                      <dt>Model</dt>
                      <dd>{profile.model}</dd>
                    </div>
                    <div>
                      <dt>Timeout Retries</dt>
                      <dd>{profile.timeout_retries}</dd>
                    </div>
                    <div>
                      <dt>API Key</dt>
                      <dd>{profile.api_key_masked}</dd>
                    </div>
                  </dl>
                  {profileTestResults[profile.id] && (
                    <code className="test-result">{profileTestResults[profile.id]}</code>
                  )}
                </div>
                <div className="item-actions">
                  <button onClick={() => editProfile(profile)} title="编辑">
                    <Pencil size={17} />
                  </button>
                  <button onClick={() => checkProfile(profile.id)} title="测试">
                    <Check size={17} />
                  </button>
                  <button onClick={() => removeProfile(profile.id)} title="删除">
                    <Trash2 size={17} />
                  </button>
                </div>
              </article>
            ))}
          </section>
        </section>
      )}

      {tab === "rooms" && (
        <section className="two-column">
          <section className="list-panel">
            {archivedRooms.length === 0 && <div className="empty-state">暂无归档房间</div>}
            {archivedRooms.map((summary) => (
              <article className="profile-item" key={summary.id}>
                <div>
                  <h3>{summary.display_name}</h3>
                  <p>
                    {phaseLabel(summary.current_phase)} · {roomStatusLabel(summary.status)}
                  </p>
                  <span>{summary.winner ? `胜方：${winnerLabel(summary.winner)}` : "已归档"}</span>
                </div>
                <div className="item-actions">
                  <button onClick={() => showEvents(summary.id)} title="查看归档">
                    <History size={17} />
                  </button>
                  <button onClick={() => showExport(summary.id)} title="导出">
                    <Download size={17} />
                  </button>
                  <button onClick={() => removeGame(summary.id)} title="删除">
                    <Trash2 size={17} />
                  </button>
                </div>
              </article>
            ))}
          </section>
          <section className="log-detail-stack">
            <ReplayDetail
              review={selectedLogReview}
              selectedGameId={selectedRoomGameId}
              selectedDisplayName={roomDetail?.game.display_name ?? ""}
            />
            {roomDetail?.game.status === "complete" && <AiUsageSummary detail={roomDetail} />}
            <section className="panel event-panel">
              <div className="section-title">
                <History size={18} />
                <h2>事件流</h2>
                {roomDetail?.game.display_name && (
                  <span className="subtle-id">{roomDetail.game.display_name}</span>
                )}
              </div>
              <div className="event-list">
                {eventLog.length === 0 && <div className="empty-state">暂无事件</div>}
                {eventLog.map((event) => (
                  <article className="event-item" key={event.id}>
                    <div className="event-meta">
                      <span>#{event.event_index}</span>
                      <strong>{eventLabel(event.event_type)}</strong>
                      <time>{formatDateTime(event.created_at)}</time>
                    </div>
                    <pre>{JSON.stringify(eventPayloadForLog(event), null, 2)}</pre>
                    <AiPromptDetails
                      event={event}
                      decision={roomDetail?.ai_decisions.find(
                        (decision) =>
                          decision.player_id === event.public_payload.player_id &&
                          decision.phase === event.public_payload.phase &&
                          decision.decision_type === event.public_payload.decision_type
                      )}
                      showPrivate={roomDetail?.game.status === "complete"}
                    />
                  </article>
                ))}
              </div>
            </section>
            <section className="panel export-panel">
              <div className="section-title">
                <Download size={18} />
                <h2>导出</h2>
              </div>
              <pre>{exportedLog || "{}"}</pre>
            </section>
          </section>
        </section>
      )}
    </main>
  );
}

function pauseForFlow() {
  return new Promise<void>((resolve) => window.setTimeout(resolve, 280));
}

function subscribeGameEvents(
  activeGameId: string,
  afterEventIndex: number,
  onEvent: (event: GameEvent) => void
) {
  const eventSource = new EventSource(gameEventsStreamUrl(activeGameId, afterEventIndex));
  eventSource.addEventListener("game-event", (message) => {
    try {
      onEvent(JSON.parse((message as MessageEvent).data) as GameEvent);
    } catch {
      // 忽略无法解析的单条事件，浏览器会继续保持 SSE 连接。
    }
  });
  return () => {
    eventSource.close();
  };
}

function latestEventIndex(events: GameEvent[]): number {
  return events.reduce((latest, event) => Math.max(latest, event.event_index), 0);
}

function PlayableRoomList({
  rooms,
  enterPlayableRoom,
  archiveRoom
}: {
  rooms: GameSummary[];
  enterPlayableRoom: (gameId: string) => void;
  archiveRoom: (gameId: string) => void;
}) {
  return (
    <section className="desk room-list-desk">
      <div className="desk-header">
        <div>
          <h2>游玩房间</h2>
          <p>选择一个未归档房间继续游玩</p>
        </div>
      </div>
      <div className="playable-room-list">
        {rooms.length === 0 && <div className="empty-state">暂无可游玩房间</div>}
        {rooms.map((summary) => (
          <article className="room-list-item" key={summary.id}>
            <div className="room-list-copy">
              <h3>{summary.display_name}</h3>
              <p>
                {phaseLabel(summary.current_phase)} · {roomStatusLabel(summary.status)}
              </p>
            </div>
            <div className="item-actions">
              <button onClick={() => enterPlayableRoom(summary.id)} title="进入游玩">
                <Play size={17} />
              </button>
              <button onClick={() => archiveRoom(summary.id)} title="归档">
                <History size={17} />
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function GameDesk({
  game,
  requiredTeamSize,
  review,
  roomDetail,
  teamAttemptNumber,
  onLeaveRoom
}: {
  game: GameState;
  requiredTeamSize: number;
  review: FlowReview;
  roomDetail: RoomDetail | null;
  teamAttemptNumber: number;
  onLeaveRoom: () => void;
}) {
  const leaderName = playerName(game, game.leader_player_id);
  return (
    <section className="desk">
      <div className="desk-header">
        <div>
          <h2>第 {game.current_round} 轮任务 · 第 {teamAttemptNumber} 次组队</h2>
          <p>
            {game.display_name} · 队长 {leaderName} · 需要 {requiredTeamSize} 人
          </p>
        </div>
        <div className="desk-header-actions">
          <button type="button" onClick={onLeaveRoom}>
            <ArrowLeft size={18} /> 返回房间
          </button>
          <div className={`winner-badge ${game.winner ?? ""}`}>
            {game.winner ? `${game.winner} wins` : phaseLabel(game.phase)}
          </div>
        </div>
      </div>
      <div className="quest-track">
        {[0, 1, 2, 3, 4].map((index) => {
          const result = game.quest_results[index];
          return (
            <span key={index} className={result ?? "pending"}>
              {index + 1}
            </span>
          );
        })}
      </div>
      <div className="seat-grid">
        {game.players.map((player) => (
          <article
            className={[
              "seat-card",
              player.is_human ? "human" : "",
              game.leader_player_id === player.id ? "leader" : "",
              game.proposed_team.includes(player.id) ? "on-team" : ""
            ].join(" ")}
            key={player.id}
          >
            <div className="seat-index">{player.seat_index + 1}</div>
            <h3>{player.name}</h3>
            {player.original_name && <p className="original-name">{player.original_name}</p>}
            <p className="visible-role">{playerRoleText(player)}</p>
            <span>{player.is_human ? "真人" : "AI"}</span>
          </article>
        ))}
      </div>
      <GameFlow game={game} review={review} roomDetail={roomDetail} />
    </section>
  );
}

function GameFlow({
  game,
  review,
  roomDetail
}: {
  game: GameState;
  review: FlowReview;
  roomDetail: RoomDetail | null;
}) {
  const playerNames = useMemo(
    () => new Map(game.players.map((player) => [player.id, player.name])),
    [game.players]
  );
  const activityText = useMemo(
    () => currentActivityText(game, playerNames),
    [game, playerNames]
  );
  const decisionsByFeedItem = useMemo(
    () => decisionsByFeedItemForReview(review, roomDetail),
    [review, roomDetail]
  );
  return (
    <section className="game-flow" aria-live="polite">
      <div className="flow-header">
        <History size={16} />
        <h3>信息流</h3>
      </div>
      <div className="game-flow-layout">
        <InformationFlowPanel
          review={review}
          activityText={activityText}
          decisionsByFeedItem={decisionsByFeedItem}
          playerNames={playerNames}
        />
      </div>
    </section>
  );
}

function InformationFlowPanel({
  review,
  activityText,
  decisionsByFeedItem,
  playerNames
}: {
  review: FlowReview;
  activityText: string;
  decisionsByFeedItem: Map<string, AiDecisionPair[]>;
  playerNames: ReadonlyMap<string, string>;
}) {
  const items = useMemo(() => feedItemsForDisplay(review), [review]);
  const [visibleItemCount, setVisibleItemCount] = useState(0);
  const feedListRef = useRef<HTMLDivElement | null>(null);
  const wasFeedAtBottomRef = useRef(true);
  const rememberFeedScrollPosition = () => {
    const element = feedListRef.current;
    if (!element) {
      wasFeedAtBottomRef.current = true;
      return;
    }
    wasFeedAtBottomRef.current =
      element.scrollHeight - element.scrollTop - element.clientHeight <= 8;
  };
  useEffect(() => {
    setVisibleItemCount((current) => {
      if (items.length <= current) {
        return items.length;
      }
      return current;
    });
  }, [items.length]);
  useEffect(() => {
    if (visibleItemCount >= items.length) {
      return;
    }
    const timeout = window.setTimeout(() => {
      setVisibleItemCount((current) => Math.min(current + 1, items.length));
    }, 220);
    return () => window.clearTimeout(timeout);
  }, [items.length, visibleItemCount]);
  const displayedItems = items.slice(0, visibleItemCount);
  useEffect(() => {
    const element = feedListRef.current;
    if (!element || !wasFeedAtBottomRef.current) {
      return;
    }
    element.scrollTop = element.scrollHeight;
  }, [displayedItems.length, activityText]);
  return (
    <section className="information-flow-panel">
      <div
        className="information-feed-list"
        onScroll={rememberFeedScrollPosition}
        ref={feedListRef}
      >
        {displayedItems.length === 0 && <div className="empty-state">暂无信息</div>}
        {displayedItems.map((item) => (
          <InformationFeedItemView
            key={item.id}
            item={item}
            decisions={decisionsByFeedItem.get(item.id) ?? []}
            playerNames={playerNames}
          />
        ))}
      </div>
      <div className="current-activity">{activityText}</div>
    </section>
  );
}

function InformationFeedItemView({
  item,
  decisions,
  playerNames
}: {
  item: InformationFeedItem;
  decisions: AiDecisionPair[];
  playerNames: ReadonlyMap<string, string>;
}) {
  if (item.type === "chat") {
    return (
      <article className="chat-item">
        <div className="chat-avatar">{item.chat.player.slice(0, 1)}</div>
        <div className="chat-bubble">
          <div className="chat-meta">
            <strong>{item.chat.player}</strong>
            <span>第 {item.chat.roundNumber} 轮</span>
            <time>{formatDateTime(item.chat.createdAt)}</time>
          </div>
          <p>{item.chat.message}</p>
          <InformationFeedDecisionDetails decisions={decisions} playerNames={playerNames} />
        </div>
      </article>
    );
  }
  const row = item.broadcast;
  return (
    <article className="announcement-item">
      <div className="announcement-bubble">
        <time>{formatDateTime(row.createdAt)}</time>
        <p>{row.text}</p>
        <InformationFeedDecisionDetails decisions={decisions} playerNames={playerNames} />
      </div>
    </article>
  );
}

function InformationFeedDecisionDetails({
  decisions,
  playerNames
}: {
  decisions: AiDecisionPair[];
  playerNames: ReadonlyMap<string, string>;
}) {
  if (decisions.length === 0) {
    return null;
  }
  return (
    <details className="feed-decision-details">
      <summary>AI 决策摘要</summary>
      <div className="feed-decision-list">
        {decisions.map((pair, index) => (
          <section className="feed-decision-card" key={`${pair.event.event_index}-${index}`}>
            <div className="feed-decision-title">
              <strong>
                {playerNames.get(valueAsString(pair.event.public_payload.player_id)) ??
                  valueAsString(pair.event.public_payload.player_id)}
              </strong>
              <span>{decisionTypeLabel(valueAsString(pair.event.public_payload.decision_type))}</span>
            </div>
            <dl className="decision-metrics compact">
              <div>
                <dt>模型</dt>
                <dd>{pair.decision?.model_name ?? "暂无数据"}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>
                  {pair.decision?.validation_status ??
                    valueAsString(pair.event.public_payload.validation_status) ??
                    "暂无数据"}
                </dd>
              </div>
              <div>
                <dt>Total tokens</dt>
                <dd>{formatNumber(pair.decision?.total_tokens)}</dd>
              </div>
              <div>
                <dt>缓存命中率</dt>
                <dd>{formatPercent(pair.decision?.cache_hit_rate ?? null)}</dd>
              </div>
            </dl>
            <p className="decision-summary-text">
              {decisionSummaryText(pair.decision, pair.event)}
            </p>
            <details className="prompt-details nested">
              <summary>查看完整 Prompt 与回答</summary>
              <AiDecisionFullDetails event={pair.event} decision={pair.decision} />
            </details>
          </section>
        ))}
      </div>
    </details>
  );
}

function ActionRoundSummary({ summaries }: { summaries: SummaryCard[] }) {
  return (
    <section className="summary-panel action-summary-panel">
      <div className="flow-subheader">本轮总结</div>
      {summaries.length === 0 && <p className="muted">暂无总结</p>}
      {summaries.map((summary) => (
        <SummaryCardView key={summary.id} summary={summary} />
      ))}
    </section>
  );
}

function SummaryCardView({ summary }: { summary: SummaryCard }) {
  return (
    <article className="summary-card">
      <strong>{summary.title}</strong>
      {summary.lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </article>
  );
}

function RoundSummary({ round }: { round: ReviewRound }) {
  return (
    <article className="round-card">
      <div className="round-title">
        <strong>第 {round.roundNumber} 轮</strong>
        {round.questResult && <span>{questResultLabel(round.questResult.result)}</span>}
      </div>
      <div className="round-grid">
        <div>
          <h4>车队</h4>
          {round.proposals.length === 0 && <p className="muted">尚未提车队</p>}
          {round.proposals.map((proposal) => (
            <p key={proposal.eventIndex}>
              {proposal.leader}：{proposal.members.join("、")}
            </p>
          ))}
        </div>
        <div>
          <h4>投票</h4>
          {!round.vote && <p className="muted">尚未公开票型</p>}
          {round.vote && (
            <>
              <p>赞成：{round.vote.approvals.join("、") || "无"}</p>
              <p>反对：{round.vote.rejections.join("、") || "无"}</p>
              <p>{round.vote.approved ? "车队通过" : "车队未通过"}</p>
            </>
          )}
        </div>
        <div>
          <h4>结算</h4>
          {!round.questResult && !round.assassination && <p className="muted">尚未结算</p>}
          {round.questResult && (
            <p>
              任务{questResultLabel(round.questResult.result)} · {round.questResult.successCount} 成功 /{" "}
              {round.questResult.failCount} 失败
            </p>
          )}
          {round.questActions.length > 0 && (
            <>
              <p>任务行动：</p>
              {round.questActions.map((action) => (
                <p key={action.eventIndex}>
                  {action.player}：{missionActionLabel(action.action)}
                </p>
              ))}
            </>
          )}
          {round.assassination && (
            <p>
              {round.assassination.assassin} 刺杀 {round.assassination.target} ·{" "}
              {winnerLabel(round.assassination.winner)}
            </p>
          )}
        </div>
      </div>
    </article>
  );
}

function ReplayDetail({
  review,
  selectedGameId,
  selectedDisplayName
}: {
  review: FlowReview;
  selectedGameId: string;
  selectedDisplayName: string;
}) {
  return (
    <section className="panel replay-panel">
      <div className="section-title">
        <History size={18} />
        <h2>完整复盘</h2>
        {selectedGameId && <span className="subtle-id">{selectedDisplayName || selectedGameId}</span>}
      </div>
      <div className="replay-rounds">
        {!selectedGameId && <div className="empty-state">选择一局查看复盘</div>}
        {selectedGameId && review.rounds.length === 0 && (
          <div className="empty-state">暂无公开复盘</div>
        )}
        {review.rounds.map((round) => (
          <RoundSummary key={round.roundNumber} round={round} />
        ))}
      </div>
      {review.chatMessages.length > 0 && (
        <div className="replay-broadcasts">
          <div className="flow-subheader">发言记录</div>
          {review.chatMessages.map((chat) => (
            <article className="chat-item" key={chat.id}>
              <div className="chat-avatar">{chat.player.slice(0, 1)}</div>
              <div className="chat-bubble">
                <div className="chat-meta">
                  <strong>{chat.player}</strong>
                  <span>第 {chat.roundNumber} 轮</span>
                  <time>{formatDateTime(chat.createdAt)}</time>
                </div>
                <p>{chat.message}</p>
              </div>
            </article>
          ))}
        </div>
      )}
      {review.summaries.length > 0 && (
        <div className="replay-broadcasts">
          <div className="flow-subheader">总结记录</div>
          {review.summaries.map((summary) => (
            <SummaryCardView key={summary.id} summary={summary} />
          ))}
        </div>
      )}
      {review.broadcasts.length > 0 && (
        <div className="replay-broadcasts">
          <div className="flow-subheader">广播记录</div>
          {review.broadcasts.map((row) => (
            <article className="broadcast-item" key={row.id}>
              <div className="flow-meta">
                <span>#{row.eventIndex}</span>
                <strong>第 {row.roundNumber} 轮</strong>
                <time>{formatDateTime(row.createdAt)}</time>
              </div>
              <p>{row.text}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function AiUsageSummary({ detail }: { detail: RoomDetail }) {
  return (
    <section className="panel usage-panel">
      <div className="section-title">
        <Bot size={18} />
        <h2>AI 用量统计</h2>
      </div>
      <div className="usage-grid">
        <UsageTable
          title="按玩家"
          rows={detail.usage_by_player.map((row) => ({
            key: row.player_id ?? row.player_name ?? "unknown-player",
            name: row.player_name ?? row.player_id ?? "未知玩家",
            totalTokens: row.total_tokens,
            averageCacheHitRate: row.average_cache_hit_rate,
            decisionCount: row.decision_count
          }))}
        />
        <UsageTable
          title="按模型"
          rows={detail.usage_by_model.map((row) => ({
            key: row.model_name ?? "unknown-model",
            name: row.model_name ?? "未知模型",
            totalTokens: row.total_tokens,
            averageCacheHitRate: row.average_cache_hit_rate,
            decisionCount: row.decision_count
          }))}
        />
      </div>
    </section>
  );
}

function UsageTable({
  title,
  rows
}: {
  title: string;
  rows: Array<{
    key: string;
    name: string;
    totalTokens: number;
    averageCacheHitRate: number | null;
    decisionCount: number;
  }>;
}) {
  return (
    <section className="usage-table">
      <h3>{title}</h3>
      {rows.length === 0 && <div className="empty-state">暂无数据</div>}
      {rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>对象</th>
              <th>决策</th>
              <th>总 token</th>
              <th>平均缓存命中</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.name}</td>
                <td>{row.decisionCount}</td>
                <td>{row.totalTokens || "暂无数据"}</td>
                <td>{formatPercent(row.averageCacheHitRate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function AiPromptDetails({
  event,
  decision,
  showPrivate
}: {
  event: GameEvent;
  decision?: AiDecisionDetail;
  showPrivate: boolean;
}) {
  if (!showPrivate || event.event_type !== "ai_decision") {
    return null;
  }
  const messages = promptMessagesForLog(event);
  if (messages.length === 0 && !decision) {
    return null;
  }
  return (
    <details className="prompt-details">
      <summary>展开 AI 决策详情</summary>
      <AiDecisionFullDetails event={event} decision={decision} />
    </details>
  );
}

function AiDecisionFullDetails({
  event,
  decision
}: {
  event: GameEvent;
  decision?: AiDecisionDetail;
}) {
  const messages = promptMessagesForLog(event);
  const outputRaw = decisionOutputRaw(event, decision);
  const outputParsed = decisionOutputParsed(event, decision);
  return (
    <>
      <dl className="decision-metrics">
        <div>
          <dt>模型</dt>
          <dd>{decision?.model_name ?? "暂无数据"}</dd>
        </div>
        <div>
          <dt>Prompt tokens</dt>
          <dd>{formatNumber(decisionNumberMetric(event, decision, "prompt_tokens"))}</dd>
        </div>
        <div>
          <dt>Completion tokens</dt>
          <dd>{formatNumber(decisionNumberMetric(event, decision, "completion_tokens"))}</dd>
        </div>
        <div>
          <dt>Total tokens</dt>
          <dd>{formatNumber(decisionNumberMetric(event, decision, "total_tokens"))}</dd>
        </div>
        <div>
          <dt>Cached tokens</dt>
          <dd>{formatNumber(decisionNumberMetric(event, decision, "cached_tokens"))}</dd>
        </div>
        <div>
          <dt>缓存命中率</dt>
          <dd>{formatPercent(decisionNumberMetric(event, decision, "cache_hit_rate"))}</dd>
        </div>
      </dl>
      {outputRaw && (
        <section className="prompt-message">
          <strong>AI 回答</strong>
          <pre>{outputRaw}</pre>
        </section>
      )}
      {outputParsed && (
        <section className="prompt-message">
          <strong>解析结果</strong>
          <pre>{JSON.stringify(outputParsed, null, 2)}</pre>
        </section>
      )}
      <div className="prompt-message-list">
        {messages.map((message, index) => (
          <section className="prompt-message" key={`${message.role}-${index}`}>
            <strong>{message.role}</strong>
            <pre>{message.content}</pre>
          </section>
        ))}
      </div>
    </>
  );
}

function StatusStrip({ game }: { game: GameState }) {
  return (
    <div className="status-strip">
      <span>{game.quest_results.filter((result) => result === "success").length} 成功</span>
      <span>{game.quest_results.filter((result) => result === "fail").length} 失败</span>
      <span>{game.votes_cast_count}/{game.players.length} 投票</span>
      <span>{game.failed_team_votes} 轮拒绝</span>
    </div>
  );
}

function StartingGameDesk({ playerCount }: { playerCount: number }) {
  return (
    <section className="desk starting-game-desk" aria-live="polite">
      <div className="desk-header">
        <div>
          <h2>对局准备中</h2>
          <p>{playerCount} 人 · 正在连接规则裁判</p>
        </div>
        <div className="winner-badge">创建中</div>
      </div>
      <div className="empty-state">游玩面板已就绪，等待首批对局信息</div>
    </section>
  );
}

function StartingActionPanel() {
  return (
    <section className="panel action-panel starting-action-panel" aria-live="polite">
      <div className="section-title">
        <Shield size={18} />
        <h2>行动</h2>
      </div>
      <div className="empty-state">正在创建房间</div>
    </section>
  );
}

function ProfileForm({
  form,
  setForm,
  isEditing
}: {
  form: LlmProfileInput;
  setForm: (form: LlmProfileInput) => void;
  isEditing: boolean;
}) {
  const update = (patch: Partial<LlmProfileInput>) => setForm({ ...form, ...patch });
  return (
    <div className="profile-form">
      <label>
        ID
        <input
          value={form.id}
          disabled={isEditing}
          onChange={(event) => update({ id: event.target.value })}
        />
      </label>
      <label>
        名称
        <input value={form.name} onChange={(event) => update({ name: event.target.value })} />
      </label>
      <label>
        Base URL
        <input
          value={form.base_url}
          onChange={(event) => update({ base_url: event.target.value })}
        />
      </label>
      <label>
        API Key
        <input
          type="text"
          value={form.api_key}
          placeholder={isEditing ? "留空则保留原密钥" : ""}
          onChange={(event) => update({ api_key: event.target.value })}
        />
      </label>
      <label>
        Model
        <input value={form.model} onChange={(event) => update({ model: event.target.value })} />
      </label>
      <label>
        Temperature
        <input
          type="number"
          min="0"
          max="2"
          step="0.1"
          value={form.temperature}
          onChange={(event) => update({ temperature: Number(event.target.value) })}
        />
      </label>
      <label>
        Timeout
        <input
          type="number"
          min="1"
          value={form.timeout}
          onChange={(event) => update({ timeout: Number(event.target.value) })}
        />
      </label>
      <label>
        Timeout Retries
        <input
          type="number"
          min="0"
          value={form.timeout_retries}
          onChange={(event) => update({ timeout_retries: Number(event.target.value) })}
        />
      </label>
    </div>
  );
}

function isRetryableAiDecisionError(error: unknown) {
  return (
    error instanceof ApiError &&
    (error.status === 502 || error.status === 504) &&
    error.message.startsWith("AI 决策失败")
  );
}

function decisionsByFeedItemForReview(
  review: FlowReview,
  roomDetail: RoomDetail | null
): Map<string, AiDecisionPair[]> {
  const result = new Map<string, AiDecisionPair[]>();
  if (!roomDetail || !isGameComplete(roomDetail.game)) {
    return result;
  }
  const events = [...roomDetail.events].sort((first, second) => first.event_index - second.event_index);
  const decisionsByAiEventIndex = pairAiDecisionEvents(events, roomDetail.ai_decisions);
  for (const item of review.feedItems) {
    const pairs = decisionPairsForFeedItem(item, events, decisionsByAiEventIndex);
    if (pairs.length > 0) {
      result.set(item.id, pairs);
    }
  }
  return result;
}

function pairAiDecisionEvents(
  events: GameEvent[],
  decisions: AiDecisionDetail[]
): Map<number, AiDecisionPair> {
  const decisionsByKey = new Map<string, AiDecisionDetail[]>();
  const decisionsByTypeKey = new Map<string, AiDecisionDetail[]>();
  for (const decision of [...decisions].sort((first, second) => first.id - second.id)) {
    const key = aiDecisionKey(decision.player_id, decision.phase, decision.decision_type);
    const typeKey = aiDecisionTypeKey(decision.player_id, decision.decision_type);
    decisionsByKey.set(key, [...(decisionsByKey.get(key) ?? []), decision]);
    decisionsByTypeKey.set(typeKey, [...(decisionsByTypeKey.get(typeKey) ?? []), decision]);
  }
  const usedByKey = new Map<string, number>();
  const usedByTypeKey = new Map<string, number>();
  const paired = new Map<number, AiDecisionPair>();
  for (const event of events) {
    if (event.event_type !== "ai_decision") {
      continue;
    }
    const key = aiDecisionKey(
      valueAsString(event.public_payload.player_id),
      valueAsString(event.public_payload.phase),
      valueAsString(event.public_payload.decision_type)
    );
    const typeKey = aiDecisionTypeKey(
      valueAsString(event.public_payload.player_id),
      valueAsString(event.public_payload.decision_type)
    );
    const used = usedByKey.get(key) ?? 0;
    const typeUsed = usedByTypeKey.get(typeKey) ?? 0;
    const decision = decisionsByKey.get(key)?.[used] ?? decisionsByTypeKey.get(typeKey)?.[typeUsed];
    paired.set(event.event_index, {
      event,
      decision
    });
    usedByKey.set(key, used + 1);
    usedByTypeKey.set(typeKey, typeUsed + 1);
  }
  return paired;
}

function decisionPairsForFeedItem(
  item: InformationFeedItem,
  events: GameEvent[],
  decisionsByAiEventIndex: Map<number, AiDecisionPair>
): AiDecisionPair[] {
  const event = events.find((candidate) => candidate.event_index === item.eventIndex);
  if (!event) {
    return [];
  }
  if (item.type === "chat") {
    return compactPairs([
      latestDecisionBeforeEvent(
        events,
        decisionsByAiEventIndex,
        event.event_index,
        valueAsString(event.public_payload.player_id),
        "speech",
        "speech"
      )
    ]);
  }
  if (item.broadcast.kind === "team_proposed") {
    return compactPairs([
      latestDecisionBeforeEventByType(
        events,
        decisionsByAiEventIndex,
        event.event_index,
        valueAsString(event.public_payload.leader_player_id),
        "team_proposal"
      )
    ]);
  }
  if (item.broadcast.kind === "lady_of_lake_used") {
    return compactPairs([
      latestDecisionBeforeEvent(
        events,
        decisionsByAiEventIndex,
        event.event_index,
        valueAsString(event.public_payload.viewer_player_id),
        "lady_of_lake",
        "lady_of_lake"
      )
    ]);
  }
  if (item.broadcast.kind === "assassination") {
    return compactPairs([
      latestDecisionBeforeEvent(
        events,
        decisionsByAiEventIndex,
        event.event_index,
        valueAsString(event.public_payload.assassin_player_id),
        "assassination",
        "assassination"
      )
    ]);
  }
  if (item.broadcast.kind === "vote_result") {
    const startIndex = latestEventIndexBefore(events, event.event_index, "team_proposed");
    return uniquePairs(
      events
        .filter(
          (candidate) =>
            candidate.event_type === "vote_cast" &&
            candidate.event_index > startIndex &&
            candidate.event_index <= event.event_index
        )
        .map((candidate) =>
          latestDecisionBeforeEvent(
            events,
            decisionsByAiEventIndex,
            candidate.event_index,
            valueAsString(candidate.public_payload.player_id),
            "voting",
            "vote"
          )
        )
    );
  }
  if (item.broadcast.kind === "quest_result") {
    const startIndex = latestEventIndexBefore(events, event.event_index, "vote_result");
    return uniquePairs(
      events
        .filter(
          (candidate) =>
            candidate.event_type === "quest_action_submitted" &&
            candidate.event_index > startIndex &&
            candidate.event_index <= event.event_index
        )
        .map((candidate) =>
          latestDecisionBeforeEvent(
            events,
            decisionsByAiEventIndex,
            candidate.event_index,
            valueAsString(candidate.public_payload.player_id),
            "quest",
            "mission_action"
          )
        )
    );
  }
  return [];
}

function latestDecisionBeforeEvent(
  events: GameEvent[],
  decisionsByAiEventIndex: Map<number, AiDecisionPair>,
  eventIndex: number,
  playerId: string,
  phase: string,
  decisionType: string
): AiDecisionPair | null {
  const key = aiDecisionKey(playerId, phase, decisionType);
  const aiEvent = [...events]
    .reverse()
    .find(
      (event) =>
        event.event_type === "ai_decision" &&
        event.event_index < eventIndex &&
        aiDecisionKey(
          valueAsString(event.public_payload.player_id),
          valueAsString(event.public_payload.phase),
          valueAsString(event.public_payload.decision_type)
        ) === key
    );
  return aiEvent ? decisionsByAiEventIndex.get(aiEvent.event_index) ?? null : null;
}

function latestDecisionBeforeEventByType(
  events: GameEvent[],
  decisionsByAiEventIndex: Map<number, AiDecisionPair>,
  eventIndex: number,
  playerId: string,
  decisionType: string
): AiDecisionPair | null {
  const aiEvent = [...events]
    .reverse()
    .find(
      (event) =>
        event.event_type === "ai_decision" &&
        event.event_index < eventIndex &&
        valueAsString(event.public_payload.player_id) === playerId &&
        valueAsString(event.public_payload.decision_type) === decisionType
    );
  return aiEvent ? decisionsByAiEventIndex.get(aiEvent.event_index) ?? null : null;
}

function latestEventIndexBefore(events: GameEvent[], eventIndex: number, eventType: string) {
  return Math.max(
    0,
    ...events
      .filter((event) => event.event_type === eventType && event.event_index < eventIndex)
      .map((event) => event.event_index)
  );
}

function compactPairs(pairs: Array<AiDecisionPair | null>) {
  return pairs.filter((pair): pair is AiDecisionPair => pair !== null);
}

function uniquePairs(pairs: Array<AiDecisionPair | null>) {
  const seen = new Set<number>();
  return compactPairs(pairs).filter((pair) => {
    if (seen.has(pair.event.event_index)) {
      return false;
    }
    seen.add(pair.event.event_index);
    return true;
  });
}

function aiDecisionKey(playerId: string, phase: string, decisionType: string) {
  return `${playerId}:${phase}:${decisionType}`;
}

function aiDecisionTypeKey(playerId: string, decisionType: string) {
  return `${playerId}:${decisionType}`;
}

function isGameComplete(game: Pick<GameState, "status" | "phase" | "winner">) {
  return game.status === "complete" || game.phase === "complete" || Boolean(game.winner);
}

function playerName(game: GameState, playerId: string) {
  return game.players.find((player) => player.id === playerId)?.name ?? playerId;
}

function playerRoleText(player: PlayerView) {
  const role = player.revealed_role ?? player.visible_role;
  return role ? roleLabel(role) : "身份未公开";
}

function currentTeamAttemptNumber(game: GameState, review: FlowReview) {
  const currentRound = review.rounds.find((round) => round.roundNumber === game.current_round);
  return currentRound?.team?.attemptNumber ?? game.failed_team_votes + 1;
}

function questResultLabel(result: string) {
  const labels: Record<string, string> = {
    success: "成功",
    fail: "失败"
  };
  return labels[result] ?? result;
}

function winnerLabel(winner: string | null) {
  const labels: Record<string, string> = {
    good: "好人胜利",
    evil: "坏人胜利"
  };
  return winner ? labels[winner] ?? `${winner} 胜利` : "胜负未定";
}

function roomStatusLabel(status: string) {
  const labels: Record<string, string> = {
    active: "正在游玩中",
    complete: "已结束",
    error_paused: "出现错误暂停"
  };
  return labels[status] ?? status;
}

function roleLabel(role: string) {
  const labels: Record<string, string> = {
    merlin: "梅林",
    percival: "派西维尔",
    assassin: "刺客",
    morgana: "莫甘娜",
    mordred: "莫德雷德",
    oberon: "奥伯伦",
    minion: "爪牙",
    loyal_servant: "忠臣",
    tristan: "崔斯坦",
    isolde: "伊索尔德",
    unknown_evil: "坏人",
    unknown_merlin: "梅林候选"
  };
  return labels[role] ?? role;
}

function factionLabel(faction: string) {
  const labels: Record<string, string> = {
    good: "好人",
    evil: "坏人"
  };
  return labels[faction] ?? faction;
}

function phaseLabel(phase: string) {
  const labels: Record<string, string> = {
    team_proposal: "组队",
    speech: "发言",
    voting: "投票",
    quest: "任务",
    assassination: "刺杀",
    complete: "结束"
  };
  return labels[phase] ?? phase;
}

function eventLabel(eventType: string) {
  const labels: Record<string, string> = {
    game_created: "开局",
    roles_assigned: "身份分配",
    private_view_recorded: "私有视图",
    team_proposed: "组队",
    speech: "发言",
    vote_cast: "投票",
    vote_result: "投票公开",
    quest_action_submitted: "任务行动",
    quest_result: "任务结算",
    assassination: "刺杀",
    ai_decision: "AI 决策"
  };
  return labels[eventType] ?? eventType;
}

function decisionTypeLabel(decisionType: string) {
  const labels: Record<string, string> = {
    team_proposal: "组队决策",
    speech: "发言决策",
    vote: "投票决策",
    mission_action: "任务行动",
    assassination: "刺杀决策",
    lady_of_lake: "湖中仙女"
  };
  return labels[decisionType] ?? decisionType;
}

function eventPayloadForLog(event: GameEvent) {
  if (event.event_type !== "quest_action_submitted") {
    return event.public_payload;
  }
  return {
    ...event.public_payload,
    mission_action: event.private_payload?.mission_action
  };
}

function decisionOutputRaw(event: GameEvent, decision: AiDecisionDetail | undefined) {
  const outputRaw = event.private_payload?.output_raw;
  return decision?.output_raw ?? (typeof outputRaw === "string" ? outputRaw : "");
}

function decisionOutputParsed(event: GameEvent, decision: AiDecisionDetail | undefined) {
  if (decision?.output_parsed) {
    return decision.output_parsed;
  }
  const parsed = event.private_payload?.output_parsed;
  return isRecord(parsed) ? parsed : null;
}

function decisionNumberMetric(
  event: GameEvent,
  decision: AiDecisionDetail | undefined,
  key:
    | "prompt_tokens"
    | "completion_tokens"
    | "total_tokens"
    | "cached_tokens"
    | "cache_hit_rate"
) {
  const fromDecision = decision?.[key];
  if (typeof fromDecision === "number") {
    return fromDecision;
  }
  const fromEvent = event.private_payload?.[key];
  return typeof fromEvent === "number" ? fromEvent : null;
}

function decisionSummaryText(decision: AiDecisionDetail | undefined, event: GameEvent) {
  const candidates = [
    decision?.strategy_summary,
    textFromRecord(decision?.output_parsed, "private_reason_summary"),
    textFromRecord(decision?.output_parsed, "public_reason"),
    textFromRecord(decision?.output_parsed, "public_message"),
    textFromRecord(decision?.output, "private_reason_summary"),
    textFromRecord(decision?.output, "public_reason"),
    textFromRecord(decision?.output, "public_message"),
    valueAsString(event.public_payload.strategy_summary)
  ].filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  return candidates[0] ?? "暂无摘要";
}

function textFromRecord(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

function valueAsString(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

function promptMessagesForLog(event: GameEvent) {
  const value = event.private_payload?.prompt_messages;
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!isRecord(item)) {
      return [];
    }
    const role = typeof item.role === "string" ? item.role : "";
    const content = typeof item.content === "string" ? item.content : "";
    return role && content ? [{ role, content }] : [];
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function missionActionLabel(action: string) {
  return questResultLabel(action);
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString();
}

function formatNumber(value: number | null | undefined) {
  return typeof value === "number" ? String(value) : "暂无数据";
}

function formatPercent(value: number | null | undefined) {
  return typeof value === "number" ? `${Math.round(value * 1000) / 10}%` : "暂无数据";
}
