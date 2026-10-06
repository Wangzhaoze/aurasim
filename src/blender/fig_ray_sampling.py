import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

plt.rcParams["font.family"] = "Times New Roman"
plt.rcParams["mathtext.fontset"] = "stix"

N = 1000
PHI_G = (1.0 + np.sqrt(5.0)) / 2.0


def uniform_angular_sampling(n=500):
    """Uniform in spherical angles (intentionally nonuniform in solid angle)."""
    n_theta = 20
    n_phi = n // n_theta
    assert n_theta * n_phi == n
    theta = np.linspace(0.035, np.pi - 0.035, n_theta)
    phi = np.linspace(0.0, 2.0 * np.pi, n_phi, endpoint=False)
    th, ph = np.meshgrid(theta, phi, indexing="ij")
    x = np.sin(th) * np.cos(ph)
    y = np.sin(th) * np.sin(ph)
    z = np.cos(th)
    return x.ravel(), y.ravel(), z.ravel()


def fibonacci_full_sphere(n=500):
    idx = np.arange(n, dtype=float)
    z = 1.0 - 2.0 * idx / (n - 1.0)
    phi = 2.0 * np.pi * np.mod(idx / PHI_G, 1.0)
    rho = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    return rho * np.cos(phi), rho * np.sin(phi), z


def fibonacci_forward_hemisphere(n=500):
    """FOV Fibonacci sampling for a +/-90° FoV around +y (forward hemisphere)."""
    idx = np.arange(n, dtype=float)
    cos_alpha = 1.0 - (idx + 0.5) / n
    sin_alpha = np.sqrt(np.maximum(0.0, 1.0 - cos_alpha * cos_alpha))
    phi = 2.0 * np.pi * np.mod(idx / PHI_G, 1.0)
    y = cos_alpha
    x = sin_alpha * np.cos(phi)
    z = sin_alpha * np.sin(phi)
    return x, y, z


def draw_sphere_guides(ax, fov_only=False):
    t = np.linspace(0.0, 2.0 * np.pi, 300)
    ax.plot(np.cos(t), np.sin(t), np.zeros_like(t),
            linewidth=0.55, linestyle="--", alpha=0.55)

    for phi0 in (0.0, np.pi / 2.0):
        theta = np.linspace(0.0, np.pi, 220)
        x = np.sin(theta) * np.cos(phi0)
        y = np.sin(theta) * np.sin(phi0)
        z = np.cos(theta)
        ax.plot(x, y, z, linewidth=0.55, linestyle="--", alpha=0.55)

    theta = np.linspace(0.0, np.pi, 300)
    ax.plot(np.sin(theta), np.zeros_like(theta), np.cos(theta),
            linewidth=0.8, alpha=0.9)
    ax.plot(np.zeros_like(theta), np.sin(theta), np.cos(theta),
            linewidth=0.55, linestyle="--", alpha=0.5)

    if fov_only:
        ax.plot(np.cos(t), np.zeros_like(t), np.sin(t),
                linewidth=1.0, alpha=0.95)


def draw_radar_board(ax):
    half_w, half_h, half_t = 1.2, 1.2, 0.035
    fy, by = +half_t, -half_t

    front = [(-half_w, fy, -half_h), (+half_w, fy, -half_h),
             (+half_w, fy, +half_h), (-half_w, fy, +half_h)]
    back = [(-half_w, by, -half_h), (+half_w, by, -half_h),
            (+half_w, by, +half_h), (-half_w, by, +half_h)]
    sides = [
        [front[0], front[1], back[1], back[0]],
        [front[1], front[2], back[2], back[1]],
        [front[2], front[3], back[3], back[2]],
        [front[3], front[0], back[0], back[3]],
    ]

    body = Poly3DCollection(
        [front, back] + sides,
        facecolors=["0.88", "0.97", "0.82", "0.84", "0.86", "0.84"],
        edgecolors="k",
        linewidths=0.8
    )
    ax.add_collection3d(body)

    xs = np.linspace(-0.23, 0.23, 4)
    zs = np.linspace(-0.12, 0.12, 2)
    pw, ph = 0.082, 0.068
    py = fy + 0.008
    faces = []

    for xc in xs:
        for zc in zs:
            faces.append([
                (xc - pw / 2, py, zc - ph / 2),
                (xc + pw / 2, py, zc - ph / 2),
                (xc + pw / 2, py, zc + ph / 2),
                (xc - pw / 2, py, zc + ph / 2)
            ])

    patches = Poly3DCollection(
        faces,
        facecolors="0.98",
        edgecolors="k",
        linewidths=0.45
    )
    ax.add_collection3d(patches)


def draw_center_frame(ax):
    origin = np.array([0.0, 0.05, 0.0])
    L = 0.62
    ax.quiver(*origin, -L, 0, 0, arrow_length_ratio=0.10, linewidth=1.8, color='g')
    ax.quiver(*origin, 0, L, 0, arrow_length_ratio=0.10, linewidth=2.2, color='r')
    ax.quiver(*origin, 0, 0, L, arrow_length_ratio=0.10, linewidth=1.8, color='b')
    ax.text(-(L + 0.05), 0.05, 0.0, r"$y$", fontsize=11, color='g')
    ax.text(0.0, L + 0.09, 0.0, r"$x$", fontsize=11, color='r')
    ax.text(0.0, 0.05, L + 0.06, r"$z$", fontsize=11, color='b')


def draw_example_rays(ax, x, y, z, fov=False):
    pts = np.column_stack([x, y, z])
    if fov:
        order = np.argsort(z)
        inds = order[np.linspace(40, len(order) - 40, 7, dtype=int)]
    else:
        inds = np.linspace(0, len(pts) - 1, 8, dtype=int)

    for i in inds:
        p = pts[i] * 0.82
        ax.quiver(0.0, 0.07, 0.0, p[0], p[1] - 0.07, p[2],
                  arrow_length_ratio=0.08, linewidth=0.65, alpha=0.9)


def style_axis(ax):
    ax.set_xlim(-1.08, 1.08)
    ax.set_ylim(-1.08, 1.08)
    ax.set_zlim(-1.08, 1.08)
    ax.set_box_aspect((1, 1, 1))
    ax.set_proj_type("ortho")
    ax.view_init(elev=15, azim=45)
    ax.set_axis_off()


def add_fov_markers(ax):
    ax.plot([0, 0], [0, 0], [0, 1.02], linewidth=0.85, linestyle="--")
    ax.plot([0, 0], [0, 0], [0, -1.02], linewidth=0.85, linestyle="--")
    ax.text(0.0, 0.03, 1.08, r"$+90^\circ$", fontsize=9, ha="center")
    ax.text(0.0, 0.03, -1.08, r"$-90^\circ$", fontsize=9, ha="center")
    ax.text(0.0, 0.78, 0.72, "Radar FoV", fontsize=10)


def draw_single_panel(ax, x, y, z, title, fov_only=False):
    style_axis(ax)
    draw_sphere_guides(ax, fov_only=fov_only)
    ax.scatter(x, y, z, s=4.0, depthshade=False, alpha=0.95)

    # 如果以后想恢复雷达板或黑箭头，把下面注释去掉
    # draw_radar_board(ax)
    draw_center_frame(ax)
    # draw_example_rays(ax, x, y, z, fov=fov_only)

    ax.set_title(title, fontsize=12, fontweight="bold", pad=2)

    if fov_only:
        # add_fov_markers(ax)
        pass


def save_combined_figure():
    samples = [
        uniform_angular_sampling(N),
        fibonacci_full_sphere(N),
        fibonacci_forward_hemisphere(N),
    ]
    titles = [
        "(a) Uniform angular sampling",
        "(b) Fibonacci sampling",
        "(c) FOV Fibonacci sampling",
    ]

    fig = plt.figure(figsize=(12, 9))
    axes = [fig.add_subplot(1, 3, i + 1, projection="3d") for i in range(3)]

    for i, (ax, (x, y, z), title) in enumerate(zip(axes, samples, titles)):
        draw_single_panel(ax, x, y, z, title, fov_only=(i == 2))

    fig.subplots_adjust(left=0.03, right=0.97, top=0.90, bottom=0.20, wspace=-0.10)
    fig.savefig("ray_sampling_comparison_v2.png", dpi=300, bbox_inches="tight", pad_inches=0.04)
    plt.show()


def save_single_figures():
    panels = [
        (uniform_angular_sampling(N), "(a) Uniform angular sampling", False, "uniform_angular_sampling.png"),
        (fibonacci_full_sphere(N), "(b) Fibonacci sampling", False, "fibonacci_sampling.png"),
        (fibonacci_forward_hemisphere(N), "(c) FOV Fibonacci sampling", True, "fov_fibonacci_sampling.png"),
    ]

    for (x, y, z), title, fov_only, filename in panels:
        fig = plt.figure(figsize=(4.6, 4.6))
        ax = fig.add_subplot(1, 1, 1, projection="3d")
        draw_single_panel(ax, x, y, z, title, fov_only=fov_only)
        fig.subplots_adjust(left=0.02, right=0.98, top=0.90, bottom=0.02)
        fig.savefig(filename, dpi=300, bbox_inches="tight", pad_inches=0.04)
        plt.close(fig)


def main():
    save_combined_figure()
    save_single_figures()


if __name__ == "__main__":
    main()