import argparse

import torch
import numpy as np
import sys
import os
import dlib

sys.path.append(".")
sys.path.append("..")

from configs import data_configs, paths_config
from datasets.inference_dataset import InferenceDataset
from torch.utils.data import DataLoader
from utils.model_utils import setup_model
from utils.common import tensor2im
from utils.alignment import align_face
from PIL import Image

import pickle


def main(args):
    net, opts = setup_model(args.ckpt, device)
    is_cars = 'car' in opts.dataset_type

    get_latents_for_ids(args, opts, net)

def setup_data_loader(args, opts):
    dataset_args = data_configs.DATASETS[opts.dataset_type]
    transforms_dict = dataset_args['transforms'](opts).get_transforms()
    images_path = args.images_dir if args.images_dir is not None else dataset_args['test_source_root']
    print(f"images path: {images_path}")
    align_function = None
    if args.align:
        align_function = run_alignment
    test_dataset = InferenceDataset(root=images_path,
                                    transform=transforms_dict['transform_test'],
                                    preprocess=align_function,
                                    opts=opts)

    data_loader = DataLoader(test_dataset,
                             batch_size=args.batch,
                             shuffle=False,
                             num_workers=2,
                             drop_last=True)

    print(f'dataset length: {len(test_dataset)}')

    if args.n_sample is None:
        args.n_sample = len(test_dataset)
    return args, data_loader


def get_latents(net, x, is_cars=False):
    codes = net.encoder(x)
    if net.opts.start_from_latent_avg:
        if codes.ndim == 2:
            codes = codes + net.latent_avg.repeat(codes.shape[0], 1, 1)[:, 0, :]
        else:
            codes = codes + net.latent_avg.repeat(codes.shape[0], 1, 1)
    if codes.shape[1] == 18 and is_cars:
        codes = codes[:, :16, :]
    return codes


def get_all_latents(net, data_loader, n_images=None, is_cars=False):
    all_latents = []
    i = 0
    with torch.no_grad():
        for batch in data_loader:
            if n_images is not None and i > n_images:
                break
            x = batch
            inputs = x.to(device).float()
            latents = get_latents(net, inputs, is_cars)
            all_latents.append(latents)
            i += len(latents)
    return torch.cat(all_latents)

def get_latents_for_ids(args, opts, net):
    from pathlib import Path
    from tqdm import tqdm

    dataset_args = data_configs.DATASETS[opts.dataset_type]
    transforms_dict = dataset_args['transforms'](opts).get_transforms()

    images_path = args.images_dir
    mg_id_dir = args.img_id_dir
    out_dir = Path(args.save_dir)

    out_dir.mkdir(exist_ok=True, parents=True)
    out_dir.joinpath('aligned').mkdir(exist_ok=True, parents=True)
    out_dir.joinpath('latents').mkdir(exist_ok=True, parents=True)
    out_dir.joinpath('inversions').mkdir(exist_ok=True, parents=True)

    generator = net.decoder
    generator.eval()

    print(f"images path: {images_path}")
    # image_ids = [int(os.path.splitext(os.path.basename(file_name))[0]) for file_name in os.listdir(args.img_id_dir)]
    for src_img_path in tqdm(Path(args.images_dir).iterdir()):
        try:
            # src_img_path = os.path.join(images_path, str(img_id).zfill(5) + ".jpg")

            if args.align:
                img = run_alignment(str(src_img_path))
                aligned_path = Path(out_dir).joinpath('aligned', src_img_path.name)
                img.save(aligned_path)
            else:
                img = Image.open(src_img_path)

            img = transforms_dict['transform_test'](img).to(device).float().unsqueeze(0)
            name = Path(src_img_path).name
            latents = get_latents(net, img).detach()

            out_latent_file = Path(out_dir).joinpath('latents', src_img_path.name).with_suffix('.pickle')
            generate_inversions(args, generator, latents, is_cars=False, name=name)
            with open(out_latent_file, 'wb') as fp:
                pickle.dump(latents, fp)
        except Exception as e:
            print(f'failed for {src_img_path}, because: {e}. Continue..')

def save_image(img, save_dir, name):
    result = tensor2im(img)
    im_save_path = os.path.join(save_dir, name)
    Image.fromarray(np.array(result)).save(im_save_path)


@torch.no_grad()
def generate_inversions(args, g, latent_codes, is_cars, name):
    # print('Saving inversion images')
    inversions_directory_path = os.path.join(args.save_dir, 'inversions')
    os.makedirs(inversions_directory_path, exist_ok=True)
    for i in range(1):
        imgs, _ = g([latent_codes[i].unsqueeze(0)], input_is_latent=True, randomize_noise=False, return_latents=True)
        if is_cars:
            imgs = imgs[:, :, 64:448, :]
        save_image(imgs[0], inversions_directory_path, name)


def run_alignment(image_path):
    predictor = dlib.shape_predictor(paths_config.model_paths['shape_predictor'])
    aligned_image = align_face(filepath=image_path, predictor=predictor)
    # print("Aligned image has shape: {}".format(aligned_image.size))
    return aligned_image


if __name__ == "__main__":
    device = "cuda"

    parser = argparse.ArgumentParser(description="Inference")
    parser.add_argument("--images_dir", type=str, default=None,
                        help="The directory of the images to be inverted")
    parser.add_argument("--img_id_dir", type=str, default=None,
                        help="The directory with files to parse for ids")

    parser.add_argument("--save_dir", type=str, default=None,
                        help="The directory to save the latent codes and inversion images. (default: images_dir")
    parser.add_argument("--batch", type=int, default=1, help="batch size for the generator")
    parser.add_argument("--n_sample", type=int, default=None, help="number of the samples to infer.")
    parser.add_argument("--latents_only", action="store_true", help="infer only the latent codes of the directory")
    parser.add_argument("--align", action="store_true", help="align face images before inference")
    parser.add_argument("ckpt", metavar="CHECKPOINT", help="path to generator checkpoint")

    args = parser.parse_args()
    main(args)
