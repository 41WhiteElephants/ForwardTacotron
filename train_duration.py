import argparse
import itertools
from pathlib import Path

import os
import torch
from torch import optim
from torch.utils.data.dataloader import DataLoader

from models.duration_predictor import DurationPredictorModel
from models.forward_tacotron import ForwardTacotron, DurationPredictor
from models.tacotron import Tacotron
from trainer.duration_trainer import DurationTrainer
from trainer.forward_trainer import ForwardTrainer
from utils import hparams as hp
from utils.checkpoints import restore_checkpoint
from utils.dataset import get_tts_datasets
from utils.display import *
from utils.paths import Paths
from utils.text.symbols import phonemes


def create_gta_features(model: Tacotron,
                        train_set: DataLoader,
                        val_set: DataLoader,
                        save_path: Path):
    model.eval()
    device = next(model.parameters()).device  # use same device as model parameters
    iters = len(train_set) + len(val_set)
    dataset = itertools.chain(train_set, val_set)
    for i, (x, mels, ids, mel_lens, dur) in enumerate(dataset, 1):
        x, mels, dur = x.to(device), mels.to(device), dur.to(device)
        with torch.no_grad():
            _, gta, _ = model(x, mels, dur)
        gta = gta.cpu().numpy()
        for j, item_id in enumerate(ids):
            mel = gta[j][:, :mel_lens[j]]
            np.save(str(save_path/f'{item_id}.npy'), mel, allow_pickle=False)
        bar = progbar(i, iters)
        msg = f'{bar} {i}/{iters} Batches '
        stream(msg)

if __name__ == '__main__':
    # Parse Arguments
    parser = argparse.ArgumentParser(description='Train Tacotron TTS')
    parser.add_argument('--force_gta', '-g', action='store_true', help='Force the model to create GTA features')
    parser.add_argument('--force_cpu', '-c', action='store_true', help='Forces CPU-only training, even when in CUDA capable environment')
    parser.add_argument('--hp_file', metavar='FILE', default='hparams.py', help='The file to use for the hyperparameters')
    args = parser.parse_args()

    hp.configure(args.hp_file)  # Load hparams from file

    paths = Paths(hp.data_path, hp.voc_model_id, hp.tts_model_id)
    assert len(os.listdir(paths.alg)) > 0, f'Could not find alignment files in {paths.alg}, please predict ' \
                                           f'alignments first with python train_tacotron.py --force_align!'

    force_gta = args.force_gta

    if not args.force_cpu and torch.cuda.is_available():
        device = torch.device('cuda')
        for session in hp.forward_schedule:
            _, _, batch_size = session
            if batch_size % torch.cuda.device_count() != 0:
                raise ValueError('`batch_size` must be evenly divisible by n_gpus!')
    else:
        device = torch.device('cpu')
    print('Using device:', device)

    # Instantiate Forward TTS Model
    print('\nInitialising Duration Model...\n')
    model = DurationPredictorModel(embed_dims=hp.forward_embed_dims,
                                   num_chars=len(phonemes),
                                   bits=hp.durpred_bits,
                                   conv_dims=hp.durpred_conv_dims,
                                   rnn_dims=hp.durpred_rnn_dims,
                                   dropout=hp.durpred_dropout).to(device)

    model_parameters = filter(lambda p: p.requires_grad, model.parameters())
    params = sum([np.prod(p.size()) for p in model_parameters])
    print(f'num params {params}')

    optimizer = optim.Adam(model.parameters())
    restore_checkpoint('duration', paths, model, optimizer, create_if_missing=True)

    trainer = DurationTrainer(paths)
    trainer.train(model, optimizer)

