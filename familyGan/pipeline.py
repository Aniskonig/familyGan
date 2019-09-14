import os
import random
import string
from os.path import join
from typing import Optional, List, Tuple

import numpy as np
from PIL import Image
import pickle

from familyGan import config
from familyGan.models.simple_avarage import SimpleAverageModel
from familyGan.multiproc_util import parmap
from familyGan.stylegan_encoder.encoder.generator_model import Generator
from familyGan.stylegan_encoder.encoder.perceptual_model import PerceptualModel
from familyGan.stylegan_encoder.ffhq_dataset.face_alignment import image_align_from_image

os.environ['TF_ENABLE_AUTO_MIXED_PRECISION'] = '1'
from auto_tqdm import tqdm


coef = -1.5


def align_image(img):
    face_landmarks = config.landmarks_detector.get_landmarks_from_image(np.array(img))
    aligned_img = image_align_from_image(img, face_landmarks)
    return aligned_img.resize((256, 256))


def image2latent(img, iterations=750, learning_rate=1.,
                 init_dlatent: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
    generated_img_list, latent_list = image_list2latent([img], iterations, learning_rate, init_dlatent)

    return generated_img_list[0], latent_list[0]


def image_list2latent(img_list, iterations=750, learning_rate=1.,
                      init_dlatent: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
    """
    :return: sizes of (batch_size, img_height, img_width, 3), (batch_size, 18, 512)
    """
    config.init_generator(init_dlatent=init_dlatent)
    generator = config.generator  # TODO: maybe
    # generator = Generator(config.Gs_network, batch_size=2)
    generator.reset_dlatents()
    perceptual_model = PerceptualModel(256, batch_size=2)
    perceptual_model.build_perceptual_model(generator.generated_image)

    perceptual_model.set_reference_images_from_image(np.array([np.array(im) for im in img_list]))
    op = perceptual_model.optimize(generator.dlatent_variable, iterations=iterations, learning_rate=learning_rate)
    with tqdm(total=iterations) as pbar:
        for iteration, loss in enumerate(op):
            pbar.set_description('Loss: %.2f' % loss)
            pbar.update()
    print(f"final loss {loss}")
    generated_img_list = generator.generate_images()
    latent_list = generator.get_dlatents()

    return generated_img_list, latent_list


def predict(father_latent, mother_latent):
    model = SimpleAverageModel(coef=coef)
    child_latent = model.predict([father_latent], [mother_latent])

    return child_latent


def latent2image(latent) -> Image.Image:
    latent = latent.reshape((1, 18, 512))
    return latent_list2image_list([latent])[0]


def latent_list2image_list(latent_list) -> List[Image.Image]:
    config.init_generator()
    config.generator.set_dlatents(latent_list)
    img_arrays = config.generator.generate_images()
    img_list = [Image.fromarray(im, 'RGB').resize((256, 256)) for im in img_arrays]
    return img_list


def full_pipe(father, mother):
    father_latent, mother_latent = None, None
    father_hash = hash(tuple(np.array(father).flatten()))
    mother_hash = hash(tuple(np.array(mother).flatten()))

    cache_path = join(config.FAMILYGAN_DIR_PATH, 'cache', 'latent_space')
    with open(cache_path, 'rb') as handle:
        image2latent_cache = pickle.load(handle)

    if father_hash in image2latent_cache:
        father_latent = image2latent_cache[father_hash]
    if mother_hash in image2latent_cache:
        mother_latent = image2latent_cache[mother_hash]

    # to latent
    def parallel_tolatent(tpl):
        (i, aligned_image) = tpl
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # see issue #152
        os.environ["CUDA_VISIBLE_DEVICES"] = str(i)
        # config.Gs_network
        _, aligned_latent = image2latent(aligned_image)
        return aligned_latent

    print("starting latent extraction")
    if father_latent is None and mother_latent is None:
        father_aligned = align_image(father)
        mother_aligned = align_image(mother)
        father_latent, mother_latent = list(parmap(parallel_tolatent, list(enumerate([father_aligned, mother_aligned]))))
    elif father_latent is not None and mother_latent is None:
        mother_aligned = align_image(mother)
        mother_latent = list(parmap(parallel_tolatent, list(enumerate([mother_aligned]))))
    elif father_latent is None and mother_latent is not None:
        father_aligned = align_image(father)
        father_latent = list(parmap(parallel_tolatent, list(enumerate([father_aligned]))))
    print("end latent extraction")
    # _, father_latent = image2latent(father_aligned)
    # _, mother_latent = image2latent(mother_aligned)

    # cache
    image2latent_cache[father_hash] = father_latent
    image2latent_cache[mother_hash] = mother_latent
    with open(cache_path, 'wb') as handle:
        pickle.dump(image2latent_cache, handle, protocol=pickle.HIGHEST_PROTOCOL)

    # model
    child_latent = predict(father_latent, mother_latent)

    # to image
    child = latent2image(child_latent)

    return child


def integrate_with_web(path_father, path_mother):
    def randomString(stringLength=10):
        """Generate a random string of fixed length """
        letters = string.ascii_lowercase
        return ''.join(random.choice(letters) for _ in range(stringLength))

    father = Image.open(path_father)
    mother = Image.open(path_mother)

    child = full_pipe(father, mother)

    parent_path = os.path.dirname(path_father)
    random_string = randomString(30)
    child_path = join(parent_path, random_string + '.png')
    child.save(child_path)
    return random_string + '.png'

#
# if __name__ == '__main__':
#     name = 'bibi'
#     father = Image.open('/data/home/morpheus/repositories/familyGan/custom_data/bibi.png')
#     mother = Image.open('/data/home/morpheus/repositories/familyGan/custom_data/sara.png')
#     child = full_pipe(father, mother)
#     child.save(f'/data/home/morpheus/repositories/familyGan/custom_data/{name}_child_coef{str(coef)}.png')
