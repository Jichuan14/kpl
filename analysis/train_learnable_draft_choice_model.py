from __future__ import annotations



import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np





def find_repo_root(start: Path) -> Path:

    for candidate in (start, *start.parents):

        if (candidate / 'analysis' / 'exports').is_dir():

            return candidate

    raise FileNotFoundError('Could not find the repository root.')





parser = argparse.ArgumentParser(
    description="Train the team-aware learnable KPL draft-choice model."
)
parser.add_argument("--league-id", required=True, help="Target season/league ID")
parser.add_argument("--previous-seasons", type=int, default=4)
parser.add_argument("--epochs", type=int, default=20)
parser.add_argument("--seed", type=int, default=7)
args = parser.parse_args()

REPO_ROOT = find_repo_root(Path.cwd().resolve())

ANALYSIS_DIR = REPO_ROOT / 'analysis'

EXPORTS_DIR = ANALYSIS_DIR / 'exports'

FEATURES_PATH = ANALYSIS_DIR / 'hero_specialty_vectors_thermometer.json'

FEATURE_SOURCE_PATH = ANALYSIS_DIR / 'hero_features.json'

CURRENT_SEASON = args.league_id

PREVIOUS_SEASONS = args.previous_seasons

RECENCY_DECAY = 0.45

WINNING_PICK_WEIGHT = 1.5

EMBEDDING_DIM = 16

EPOCHS = args.epochs

BATCH_SIZE = 512

LEARNING_RATE = 0.02

WEIGHT_DECAY = 1e-4

SEED = args.seed

MODEL_PATH = ANALYSIS_DIR / 'outputs' / CURRENT_SEASON / 'learnable_draft_choice_model.json'

FEATURE_SPACE_PATH = ANALYSIS_DIR / 'outputs' / CURRENT_SEASON / 'learned_hero_feature_space.json'



rng = np.random.default_rng(SEED)

print('Repository:', REPO_ROOT)

print('Target/current season:', CURRENT_SEASON)
available_paths = {path.parent.name: path for path in EXPORTS_DIR.glob('*/bp_decisions.jsonl')}

available_seasons = sorted(available_paths)

if CURRENT_SEASON not in available_paths:

    raise ValueError(f'{CURRENT_SEASON} has no BP-decision export. Available: {available_seasons}')



current_position = available_seasons.index(CURRENT_SEASON)

training_seasons = available_seasons[max(0, current_position - PREVIOUS_SEASONS): current_position + 1]

if len(training_seasons) != PREVIOUS_SEASONS + 1:

    raise ValueError(f'Need {PREVIOUS_SEASONS} prior seasons plus {CURRENT_SEASON}; found {training_seasons}')



season_weights = {

    season: RECENCY_DECAY ** (len(training_seasons) - 1 - index)

    for index, season in enumerate(training_seasons)

}





def read_usable_decisions(season: str, path: Path) -> list[dict]:

    usable = []

    with path.open(encoding='utf-8') as source:

        for line_number, line in enumerate(source, start=1):

            if not line.strip():

                continue

            row = json.loads(line)

            selected = int(row.get('selected_hero_id') or 0)

            legal = {int(hero_id) for hero_id in row.get('legal_hero_ids', []) if int(hero_id) > 0}

            if (

                not row.get('is_peak_battle')

                and row.get('action') in {'pick', 'ban'}

                and row.get('acting_team_id')

                and row.get('opponent_team_id')

                and selected > 0

                and selected in legal

            ):

                row['_season_weight'] = season_weights[season]

                row['_outcome_weight'] = (

                    WINNING_PICK_WEIGHT

                    if row.get('action') == 'pick' and row.get('acting_team_won_battle') is True

                    else 1.0

                )

                row['_sample_weight'] = row['_season_weight'] * row['_outcome_weight']

                usable.append(row)

    return usable





decisions = [

    row

    for season in training_seasons

    for row in read_usable_decisions(season, available_paths[season])

]



print('Training seasons and sample weights:')

for season in training_seasons:

    print(f'  {season}: {season_weights[season]:.4f}')

print('Usable BP decisions:', len(decisions))

print('Decisions by season:', dict(sorted(Counter(row['league_id'] for row in decisions).items())))

print('Winning picks upweighted:', sum(row['_outcome_weight'] > 1 for row in decisions))
feature_artifact = json.loads(FEATURES_PATH.read_text(encoding='utf-8'))

base_feature_names = tuple(feature_artifact['feature_names'])

feature_width = len(base_feature_names) + 1

feature_names = (*base_feature_names, 'feature_known')

feature_by_id = {

    int(row['hero_id']): np.asarray(row['vector'] + [float(row['feature_known'])], dtype=np.float32)

    for row in feature_artifact['rows']

    if row['hero_id'] is not None

}



hero_ids = sorted({int(hero_id) for row in decisions for hero_id in row['legal_hero_ids'] if int(hero_id) > 0})

hero_to_index = {hero_id: index for index, hero_id in enumerate(hero_ids)}

HERO_COUNT = len(hero_ids)

team_ids = sorted({

    str(team_id)

    for row in decisions

    for team_id in (row['acting_team_id'], row['opponent_team_id'])

})

team_to_index = {team_id: index for index, team_id in enumerate(team_ids)}

TEAM_COUNT = len(team_ids)



hero_features = np.zeros((HERO_COUNT, feature_width), dtype=np.float32)

for hero_id, index in hero_to_index.items():

    if hero_id in feature_by_id:

        hero_features[index] = feature_by_id[hero_id]



ROLE_FIELDS = (

    'current_team_picks',

    'current_opponent_picks',

    'current_team_bans',

    'current_opponent_bans',

)





def make_static_state(row: dict) -> np.ndarray:

    pieces = []

    for field in ROLE_FIELDS:

        indices = [hero_to_index[int(hero_id)] for hero_id in row.get(field, []) if int(hero_id) in hero_to_index]

        vectors = hero_features[indices] if indices else np.empty((0, feature_width), dtype=np.float32)

        total = vectors.sum(axis=0) if len(vectors) else np.zeros(feature_width, dtype=np.float32)

        maximum = vectors.max(axis=0) if len(vectors) else np.zeros(feature_width, dtype=np.float32)

        pieces.extend((total, maximum, np.asarray([len(indices)], dtype=np.float32)))

    return np.concatenate(pieces)





context_keys = sorted({

    (str(row['action']), str(row['side']), int(row['team_action_type_number']))

    for row in decisions

})

context_to_index = {key: index for index, key in enumerate(context_keys)}

STATE_WIDTH = len(ROLE_FIELDS) * (2 * feature_width + 1)

N = len(decisions)



static_states = np.empty((N, STATE_WIDTH), dtype=np.float32)

source_presence = np.zeros((N, len(ROLE_FIELDS), HERO_COUNT), dtype=np.float32)

legal_mask = np.zeros((N, HERO_COUNT), dtype=bool)

targets = np.empty(N, dtype=np.int64)

contexts = np.empty(N, dtype=np.int64)

acting_teams = np.empty(N, dtype=np.int64)

opponent_teams = np.empty(N, dtype=np.int64)

sample_weights = np.empty(N, dtype=np.float32)



for row_index, row in enumerate(decisions):

    static_states[row_index] = make_static_state(row)

    for role_index, field in enumerate(ROLE_FIELDS):

        for hero_id in row.get(field, []):

            hero_index = hero_to_index.get(int(hero_id))

            if hero_index is not None:

                source_presence[row_index, role_index, hero_index] = 1.0

    for hero_id in row['legal_hero_ids']:

        hero_index = hero_to_index.get(int(hero_id))

        if hero_index is not None:

            legal_mask[row_index, hero_index] = True

    targets[row_index] = hero_to_index[int(row['selected_hero_id'])]

    contexts[row_index] = context_to_index[(str(row['action']), str(row['side']), int(row['team_action_type_number']))]

    acting_teams[row_index] = team_to_index[str(row['acting_team_id'])]

    opponent_teams[row_index] = team_to_index[str(row['opponent_team_id'])]

    sample_weights[row_index] = float(row['_sample_weight'])



assert np.all(legal_mask[np.arange(N), targets])

assert static_states.shape == (N, STATE_WIDTH)

assert source_presence.shape == (N, len(ROLE_FIELDS), HERO_COUNT)



print('Hero vocabulary:', HERO_COUNT)

print('Heroes with specialty profiles:', int(hero_features[:, -1].sum()))

print('Context types:', len(context_keys), context_keys)

print('Team vocabulary:', TEAM_COUNT)

print('Static-state width:', STATE_WIDTH)
def normal(shape, scale=0.03):

    return rng.normal(0.0, scale, size=shape).astype(np.float32)





parameters = {

    'feature_projection': normal((feature_width, EMBEDDING_DIM)),

    'hero_residual': normal((HERO_COUNT, EMBEDDING_DIM)),

    'context_embedding': normal((len(context_keys), EMBEDDING_DIM)),

    'state_projection': normal((STATE_WIDTH, EMBEDDING_DIM)),

    'source_embedding': normal((len(ROLE_FIELDS), HERO_COUNT, EMBEDDING_DIM)),

    'acting_team_embedding': normal((TEAM_COUNT, EMBEDDING_DIM)),

    'opponent_team_embedding': normal((TEAM_COUNT, EMBEDDING_DIM)),

    'hero_bias': np.zeros(HERO_COUNT, dtype=np.float32),

}

adam_m = {name: np.zeros_like(value) for name, value in parameters.items()}

adam_v = {name: np.zeros_like(value) for name, value in parameters.items()}



parameter_count = sum(value.size for value in parameters.values())

print('Learnable parameters:', parameter_count)

print('Candidate representation dimension:', EMBEDDING_DIM)
def forward(batch_indices: np.ndarray):

    candidate_representations = hero_features @ parameters['feature_projection'] + parameters['hero_residual']

    state_queries = (

        parameters['context_embedding'][contexts[batch_indices]]

        + static_states[batch_indices] @ parameters['state_projection']

        + np.einsum('brh,rhd->bd', source_presence[batch_indices], parameters['source_embedding'])

        + parameters['acting_team_embedding'][acting_teams[batch_indices]]

        + parameters['opponent_team_embedding'][opponent_teams[batch_indices]]

    )

    logits = state_queries @ candidate_representations.T + parameters['hero_bias']

    logits = np.where(legal_mask[batch_indices], logits, -1e9)

    return candidate_representations, state_queries, logits





def probabilities_from_logits(logits: np.ndarray) -> np.ndarray:

    shifted = logits - logits.max(axis=1, keepdims=True)

    exponentiated = np.exp(shifted)

    return exponentiated / exponentiated.sum(axis=1, keepdims=True)





def batch_gradients(batch_indices: np.ndarray) -> tuple[float, dict[str, np.ndarray]]:

    candidate_representations, state_queries, logits = forward(batch_indices)

    probabilities = probabilities_from_logits(logits)

    batch_targets = targets[batch_indices]

    batch_weights = sample_weights[batch_indices]

    weight_total = float(batch_weights.sum())

    correct_probabilities = probabilities[np.arange(len(batch_indices)), batch_targets]

    loss = float(-(batch_weights * np.log(np.maximum(correct_probabilities, 1e-12))).sum() / weight_total)



    d_logits = probabilities

    d_logits[np.arange(len(batch_indices)), batch_targets] -= 1.0

    d_logits *= (batch_weights / weight_total)[:, None]



    d_candidate_representations = d_logits.T @ state_queries

    d_state_queries = d_logits @ candidate_representations

    gradients = {

        'feature_projection': hero_features.T @ d_candidate_representations,

        'hero_residual': d_candidate_representations,

        'context_embedding': np.zeros_like(parameters['context_embedding']),

        'state_projection': static_states[batch_indices].T @ d_state_queries,

        'source_embedding': np.einsum('brh,bd->rhd', source_presence[batch_indices], d_state_queries),

        'acting_team_embedding': np.zeros_like(parameters['acting_team_embedding']),

        'opponent_team_embedding': np.zeros_like(parameters['opponent_team_embedding']),

        'hero_bias': d_logits.sum(axis=0),

    }

    np.add.at(gradients['context_embedding'], contexts[batch_indices], d_state_queries)

    np.add.at(gradients['acting_team_embedding'], acting_teams[batch_indices], d_state_queries)

    np.add.at(gradients['opponent_team_embedding'], opponent_teams[batch_indices], d_state_queries)

    return loss, gradients





def adam_step(gradients: dict[str, np.ndarray], step: int):

    beta1, beta2, epsilon = 0.9, 0.999, 1e-8

    for name, gradient in gradients.items():

        if name != 'hero_bias':

            gradient = gradient + WEIGHT_DECAY * parameters[name]

        adam_m[name] = beta1 * adam_m[name] + (1 - beta1) * gradient

        adam_v[name] = beta2 * adam_v[name] + (1 - beta2) * gradient * gradient

        m_hat = adam_m[name] / (1 - beta1 ** step)

        v_hat = adam_v[name] / (1 - beta2 ** step)

        parameters[name] -= LEARNING_RATE * m_hat / (np.sqrt(v_hat) + epsilon)





def evaluate(indices: np.ndarray) -> dict[str, float]:

    total_weight = 0.0

    total_loss = 0.0

    top1 = 0.0

    top5 = 0.0

    for start in range(0, len(indices), BATCH_SIZE):

        batch_indices = indices[start:start + BATCH_SIZE]

        _, _, logits = forward(batch_indices)

        probabilities = probabilities_from_logits(logits)

        batch_targets = targets[batch_indices]

        batch_weights = sample_weights[batch_indices]

        correct_probabilities = probabilities[np.arange(len(batch_indices)), batch_targets]

        total_loss += float(-(batch_weights * np.log(np.maximum(correct_probabilities, 1e-12))).sum())

        total_weight += float(batch_weights.sum())

        ranking = np.argsort(-logits, axis=1)

        top1 += float((ranking[:, 0] == batch_targets).astype(np.float32) @ batch_weights)

        top5 += float((ranking[:, :5] == batch_targets[:, None]).any(axis=1).astype(np.float32) @ batch_weights)

    return {

        'weighted_negative_log_likelihood': total_loss / total_weight,

        'weighted_top_1_accuracy': top1 / total_weight,

        'weighted_top_5_accuracy': top5 / total_weight,

    }





all_indices = np.arange(N)

global_step = 0

for epoch in range(1, EPOCHS + 1):

    shuffled = rng.permutation(all_indices)

    epoch_losses = []

    for start in range(0, N, BATCH_SIZE):

        batch_indices = shuffled[start:start + BATCH_SIZE]

        loss, gradients = batch_gradients(batch_indices)

        global_step += 1

        adam_step(gradients, global_step)

        epoch_losses.append(loss)

    if epoch == 1 or epoch % 5 == 0 or epoch == EPOCHS:

        metrics = evaluate(all_indices)

        print(

            f"epoch {epoch:02d} | batch NLL {np.mean(epoch_losses):.4f} | "

            f"full weighted NLL {metrics['weighted_negative_log_likelihood']:.4f} | "

            f"top-1 {metrics['weighted_top_1_accuracy']:.3%} | "

            f"top-5 {metrics['weighted_top_5_accuracy']:.3%}"

        )



final_metrics = evaluate(all_indices)

final_metrics
model_artifact = {

    'schema_version': 2,

    'model_type': 'team_aware_recency_weighted_hybrid_bilinear_choice',

    'target_season': CURRENT_SEASON,

    'training_seasons': training_seasons,

    'season_weights': season_weights,

    'feature_names': list(feature_names),

    'role_fields': list(ROLE_FIELDS),

    'hero_ids': hero_ids,

    'team_ids': team_ids,

    'team_training_decisions': dict(Counter(str(row['acting_team_id']) for row in decisions)),

    'context_keys': [list(key) for key in context_keys],

    'training_decisions': int(N),

    'effective_training_decisions': float(sample_weights.sum()),

    'hyperparameters': {

        'embedding_dim': EMBEDDING_DIM,

        'epochs': EPOCHS,

        'batch_size': BATCH_SIZE,

        'learning_rate': LEARNING_RATE,

        'weight_decay': WEIGHT_DECAY,

        'recency_decay': RECENCY_DECAY,

        'winning_pick_weight': WINNING_PICK_WEIGHT,

        'seed': SEED,

    },

    'training_metrics_in_sample': final_metrics,

    'parameters': {name: value.tolist() for name, value in parameters.items()},

}

MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

MODEL_PATH.write_text(json.dumps(model_artifact, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

print(f'Wrote {MODEL_PATH.relative_to(REPO_ROOT)}')

print(f'Artifact size: {MODEL_PATH.stat().st_size / 1024:.1f} KiB')
candidate_representations = hero_features @ parameters['feature_projection'] + parameters['hero_residual']

centered_representations = candidate_representations - candidate_representations.mean(axis=0, keepdims=True)

_, singular_values, principal_components = np.linalg.svd(centered_representations, full_matrices=False)

coordinates = centered_representations @ principal_components[:2].T

explained_variance = singular_values ** 2

explained_variance_ratio = (explained_variance / explained_variance.sum())[:2]



source_features = json.loads(FEATURE_SOURCE_PATH.read_text(encoding='utf-8'))

source_by_id = {int(row['hero_id']): row for row in source_features if row.get('hero_id') is not None}

hero_names = {

    int(row['selected_hero_id']): str(row.get('selected_hero_name') or row['selected_hero_id'])

    for row in decisions

}

pick_counts = Counter(int(row['selected_hero_id']) for row in decisions if row['action'] == 'pick')

ban_counts = Counter(int(row['selected_hero_id']) for row in decisions if row['action'] == 'ban')

weighted_bp_action_counts = Counter()

for row in decisions:

    weighted_bp_action_counts[int(row['selected_hero_id'])] += float(row['_sample_weight'])

distance_matrix = np.linalg.norm(

    candidate_representations[:, None, :] - candidate_representations[None, :, :],

    axis=2,

)

np.fill_diagonal(distance_matrix, np.inf)



feature_space_rows = []

for index, hero_id in enumerate(hero_ids):

    source = source_by_id.get(hero_id, {})

    nearest_indices = np.argsort(distance_matrix[index])[:5]

    feature_space_rows.append({

        'hero_id': hero_id,

        'hero_name': hero_names.get(hero_id, source.get('hero_name', str(hero_id))),

        'x': float(coordinates[index, 0]),

        'y': float(coordinates[index, 1]),

        'primary_lane': source.get('primary_lane', 'unknown'),

        'damage_types': source.get('damage_types', []),

        'feature_known': bool(hero_features[index, -1]),

        'pick_count': int(pick_counts[hero_id]),

        'ban_count': int(ban_counts[hero_id]),

        'bp_action_count': int(pick_counts[hero_id] + ban_counts[hero_id]),

        'weighted_bp_action_count': float(weighted_bp_action_counts[hero_id]),

        'nearest_hero_ids': [hero_ids[neighbor] for neighbor in nearest_indices],

    })



feature_space_artifact = {

    'schema_version': 1,

    'target_season': CURRENT_SEASON,

    'model_type': model_artifact['model_type'],

    'projection': 'pca',

    'source_space': 'learned_candidate_representation',

    'explained_variance_ratio': [float(value) for value in explained_variance_ratio],

    'rows': feature_space_rows,

}

FEATURE_SPACE_PATH.write_text(json.dumps(feature_space_artifact, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

print(f'Wrote {len(feature_space_rows)} hero coordinates to {FEATURE_SPACE_PATH.relative_to(REPO_ROOT)}')

print('PCA explained variance:', ', '.join(f'{value:.1%}' for value in explained_variance_ratio))
def score_observed_state(row: dict, limit: int = 10) -> list[dict]:

    static_state = make_static_state(row)[None, :]

    source_state = np.zeros((1, len(ROLE_FIELDS), HERO_COUNT), dtype=np.float32)

    for role_index, field in enumerate(ROLE_FIELDS):

        for hero_id in row.get(field, []):

            hero_index = hero_to_index.get(int(hero_id))

            if hero_index is not None:

                source_state[0, role_index, hero_index] = 1.0

    context = context_to_index[(str(row['action']), str(row['side']), int(row['team_action_type_number']))]

    candidate_representations = hero_features @ parameters['feature_projection'] + parameters['hero_residual']

    query = (

        parameters['context_embedding'][context]

        + (static_state @ parameters['state_projection'])[0]

        + np.einsum('rh,rhd->d', source_state[0], parameters['source_embedding'])

        + parameters['acting_team_embedding'][team_to_index[str(row['acting_team_id'])]]

        + parameters['opponent_team_embedding'][team_to_index[str(row['opponent_team_id'])]]

    )

    logits = candidate_representations @ query + parameters['hero_bias']

    mask = np.zeros(HERO_COUNT, dtype=bool)

    for hero_id in row['legal_hero_ids']:

        hero_index = hero_to_index.get(int(hero_id))

        if hero_index is not None:

            mask[hero_index] = True

    logits = np.where(mask, logits, -1e9)

    probabilities = probabilities_from_logits(logits[None, :])[0]

    ranking = np.argsort(-probabilities)[:limit]

    return [

        {

            'hero_id': hero_ids[index],

            'probability': float(probabilities[index]),

            'selected_in_source': hero_ids[index] == int(row['selected_hero_id']),

        }

        for index in ranking

    ]





example_row = next(row for row in decisions if row['league_id'] == CURRENT_SEASON and row['bp_order'] == 5)

print('Example action:', example_row['action'], example_row['side'], 'BP order', example_row['bp_order'])

print('Observed selection:', example_row['selected_hero_id'], example_row['selected_hero_name'])

score_observed_state(example_row)
