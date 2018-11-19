from matplotlib import pyplot as plt
import numpy as np

from prototype_jpeg import compress, extract


def main():
    with open('tests/images/rgb/11x20.raw', 'rb') as img_file:
        img_arr = np.fromfile(img_file, dtype=np.uint8)
        show_raw_images([img_arr], [(11, 20)])
        compressed = compress(
            img_arr,
            (11, 20),
            grey_level=False,
            quality=50,
            subsampling_mode=1
        )
        extracted = extract(compressed)
        # with np.printoptions(suppress=True):
        #     print(compressed)
        # show_raw_images(
        #     compressed.values(),
        #     ((10, 10), (5, 5), (5, 5)),
        #     grey_level=True
        # )
        # with np.printoptions(suppress=True):
        #     print(extracted)


def show_raw_images(images, sizes, titles=None, grey_level=False):
    if titles is None:
        titles = range(len(images))
    _, axarr = plt.subplots(1, len(images))

    for idx, (img, size, title) in enumerate(zip(images, sizes, titles)):
        if isinstance(img, str):
            with open(img, 'rb') as img_file:
                arr = np.fromfile(img_file, dtype=np.uint8)
        else:
            arr = np.array(img)
        arr.shape = size if grey_level else (*size, 3)
        if len(images) == 1:
            axarr.set_title(title)
            axarr.imshow(arr, cmap='gray', vmin=0, vmax=255)
        else:
            axarr[idx].set_title(title)
            axarr[idx].imshow(arr, cmap='gray', vmin=0, vmax=255)
    plt.show()


if __name__ == '__main__':
    main()
