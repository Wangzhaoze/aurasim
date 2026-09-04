from pathlib import Path
import re
import numpy as np
from PIL import Image, ImageOps
import imageio.v2 as imageio
from tqdm import tqdm, trange


# ============================================================
#                         SETTINGS
# ============================================================

# 图片所在文件夹
INPUT_DIR = r"D:/Datasets/RAMPCNN/2019_04_09_pms2000/images_0"

# 输出文件路径
# 改成 .mp4 就输出 MP4
# 改成 .gif 就输出 GIF
OUTPUT_PATH = f"{INPUT_DIR}/output.mp4"

# 视频 / GIF 帧率
FPS = 30

# 支持的图片格式
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"]

# 是否递归读取子文件夹
RECURSIVE = False

# 输出尺寸
# None：使用第一张图片的尺寸
# 例如 (1920, 1080)
OUTPUT_SIZE = None

# 图片尺寸不一致时如何处理：
# "pad"     = 保持比例，空白区域补黑边
# "crop"    = 保持比例，裁剪到目标尺寸
# "stretch" = 直接拉伸
RESIZE_MODE = "pad"

# GIF 是否循环
# 0 = 无限循环
# 1 = 播放一次
GIF_LOOP = 0

# MP4 编码器
VIDEO_CODEC = "libx264"

# MP4 质量，数值越小质量越高
# 一般推荐 18~23
CRF = 18


# ============================================================
#                       IMPLEMENTATION
# ============================================================

def natural_sort_key(path):
    """
    自然排序：
    1.png, 2.png, 10.png
    而不是：
    1.png, 10.png, 2.png
    """
    return [
        int(text) if text.isdigit() else text.lower()
        for text in re.split(r"(/d+)", str(path))
    ]


def find_images(folder):
    folder = Path(folder)

    if RECURSIVE:
        files = folder.rglob("*")
    else:
        files = folder.glob("*")

    images = [
        p for p in files
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    images.sort(key=natural_sort_key)

    return images


def resize_image(img, target_size):
    target_w, target_h = target_size

    if img.size == target_size:
        return img

    if RESIZE_MODE == "stretch":
        return img.resize(
            target_size,
            Image.Resampling.LANCZOS
        )

    elif RESIZE_MODE == "crop":
        return ImageOps.fit(
            img,
            target_size,
            method=Image.Resampling.LANCZOS
        )

    elif RESIZE_MODE == "pad":
        img.thumbnail(
            target_size,
            Image.Resampling.LANCZOS
        )

        canvas = Image.new(
            "RGB",
            target_size,
            color=(0, 0, 0)
        )

        x = (target_w - img.width) // 2
        y = (target_h - img.height) // 2

        canvas.paste(img, (x, y))

        return canvas

    else:
        raise ValueError(
            f"Unknown RESIZE_MODE: {RESIZE_MODE}"
        )


def make_even_size(size):
    """
    H.264 通常要求宽高为偶数。
    """
    w, h = size

    if w % 2 != 0:
        w -= 1

    if h % 2 != 0:
        h -= 1

    return w, h


def main():

    image_paths = find_images(INPUT_DIR)

    if not image_paths:
        raise RuntimeError(
            f"No images found in:/n{INPUT_DIR}"
        )

    print(f"Found {len(image_paths)} images.")

    output_path = Path(OUTPUT_PATH)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_suffix = output_path.suffix.lower()

    if output_suffix not in [".mp4", ".gif"]:
        raise ValueError(
            "OUTPUT_PATH must end with .mp4 or .gif"
        )

    # --------------------------------------------------------
    # 确定输出尺寸
    # --------------------------------------------------------

    with Image.open(image_paths[0]) as first_img:
        first_img = first_img.convert("RGB")

        if OUTPUT_SIZE is None:
            target_size = first_img.size
        else:
            target_size = OUTPUT_SIZE

    # MP4 使用偶数宽高
    if output_suffix == ".mp4":
        target_size = make_even_size(target_size)

    print(f"Output size : {target_size}")
    print(f"FPS         : {FPS}")
    print(f"Output      : {OUTPUT_PATH}")

    # --------------------------------------------------------
    # MP4
    # --------------------------------------------------------

    if output_suffix == ".mp4":

        writer = imageio.get_writer(
            OUTPUT_PATH,
            format="FFMPEG",   # 强制使用 FFmpeg，防止被识别成 TIFF
            mode="I",
            fps=FPS,
            codec=VIDEO_CODEC,
            ffmpeg_params=[
                "-crf", str(CRF),
                "-pix_fmt", "yuv420p"
            ]
        )

        try:
            for i, image_path in tqdm(enumerate(image_paths), total=len(image_paths)):

                with Image.open(image_path) as img:
                    img = img.convert("RGB")
                    img = resize_image(
                        img,
                        target_size
                    )

                    frame = np.asarray(img)

                writer.append_data(frame)

        finally:
            writer.close()

    # --------------------------------------------------------
    # GIF
    # --------------------------------------------------------

    elif output_suffix == ".gif":

        frames = []

        for i, image_path in tqdm(enumerate(image_paths), total=len(image_paths)):

            with Image.open(image_path) as img:
                img = img.convert("RGB")
                img = resize_image(
                    img,
                    target_size
                )

                frames.append(
                    np.asarray(img)
                )

        imageio.mimsave(
            OUTPUT_PATH,
            frames,
            duration=1.0 / FPS,
            loop=GIF_LOOP
        )

    print("/nDone!")
    print(f"Saved to:/n{OUTPUT_PATH}")


if __name__ == "__main__":
    main()