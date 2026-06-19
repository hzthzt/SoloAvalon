SCHEMA_SQL = """
create table if not exists games (
    id text primary key,
    status text not null,
    player_count integer not null,
    role_set text not null,
    enabled_options text not null default '[]',
    current_round integer not null,
    current_phase text not null,
    winner text,
    default_llm_profile_id text,
    created_at text not null,
    updated_at text not null
);

create table if not exists players (
    id text not null,
    game_id text not null,
    seat_index integer not null,
    name text not null,
    original_name text,
    is_human integer not null,
    role text not null,
    faction text not null,
    llm_profile_id text,
    primary key(game_id, id),
    foreign key(game_id) references games(id) on delete cascade,
    unique(game_id, seat_index)
);

create table if not exists game_events (
    id integer primary key autoincrement,
    game_id text not null,
    event_index integer not null,
    event_type text not null,
    public_payload text not null,
    private_payload text,
    created_at text not null,
    foreign key(game_id) references games(id) on delete cascade,
    unique(game_id, event_index)
);

create table if not exists ai_decisions (
    id integer primary key autoincrement,
    game_id text not null,
    player_id text not null,
    phase text not null,
    decision_type text not null,
    input_summary text not null,
    strategy_summary text not null,
    output text not null,
    model_name text not null,
    llm_profile_id text,
    prompt_template_name text not null,
    prompt_template_version text not null,
    context_builder_version text not null,
    stable_prefix_hash text not null,
    cache_strategy text not null,
    context_summary text not null,
    context_truncated integer not null,
    output_raw text,
    output_parsed text,
    validation_status text not null,
    created_at text not null,
    foreign key(game_id) references games(id) on delete cascade,
    foreign key(game_id, player_id) references players(game_id, id) on delete cascade
);

create table if not exists ai_memory_snapshots (
    id integer primary key autoincrement,
    game_id text not null,
    player_id text not null,
    round_number integer not null,
    phase text not null,
    memory_payload text not null,
    created_at text not null,
    foreign key(game_id) references games(id) on delete cascade,
    foreign key(game_id, player_id) references players(game_id, id) on delete cascade
);
"""
