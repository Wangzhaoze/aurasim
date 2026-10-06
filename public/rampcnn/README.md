# RAMP-CNN web frame layout

The AuRaSim project page reads all Digital Twin and Sim2Real frame data from this public directory.

Upload the same synchronized frame index to four folders per scene. The website expects **lossless PNG** files named exactly `frame_XXXX.png`, starting from `frame_0001.png`.

```text
public/rampcnn/frames/
├─ bms1000/
│  ├─ camera/
│  │  ├─ real/
│  │  │  ├─ frame_0001.png
│  │  │  ├─ frame_0002.png
│  │  │  └─ ...
│  │  └─ sim/
│  │     ├─ frame_0001.png
│  │     ├─ frame_0002.png
│  │     └─ ...
│  └─ radar/
│     ├─ real/
│     │  ├─ frame_0001.png
│     │  ├─ frame_0002.png
│     │  └─ ...
│     └─ sim/
│        ├─ frame_0001.png
│        ├─ frame_0002.png
│        └─ ...
├─ pms2000/
│  └─ camera/{real,sim}/... and radar/{real,sim}/...
└─ mlms001/
   └─ camera/{real,sim}/... and radar/{real,sim}/...
```

## Current synchronized frame counts

These are configured in `src/components/rampCnnFrameConfig.ts`:

- BMS1000: 896
- PMS2000: 894
- MLMS001: 898

If the regenerated runs contain a different number of matched Camera/Radar frames, only change the `frames` value for the corresponding scene in that config file.

## How the page uses these files

- **Digital Twin Scenarios** uses only `camera/real` and `camera/sim`, with Split and Blend modes plus a frame scrubber.
- **Sim2Real** shows three scene rows and four columns: Camera Real, Camera AuRaSim, Radar Real, Radar AuRaSim. One progress bar per scene row scrubs all four images synchronously.
- Camera frames are deliberately shared between the two sections, so they are uploaded only once.

## PNG and runtime performance

PNG is intentionally used here because the visual result should remain pixel-lossless. Repository size is not the primary runtime concern: browser smoothness depends mostly on transferred bytes, image decode time, and how aggressively the UI changes frame URLs while the slider is dragged.

Keep the original scientific content and resolution if desired. Lossless PNG optimizers such as `optipng`, `pngcrush`, or `zopfli` are safe because they reduce file size without changing pixels. If first-pass scrubbing over the network is not smooth enough, optimize the viewer with nearby-frame prefetching, request throttling while dragging, and browser caching rather than switching to lossy image compression.

## Page backup

The project page immediately before the frame-scrubber redesign is preserved on branch:

`backup/pre-frame-scrubber-2026-10-06`

at commit:

`31fa50084506890037b1d4882037961d895cad67`

That branch preserves the old Digital Twin static comparisons and the previous RAMP-CNN Sim2Real video layout. The current `main` branch and any new data uploaded under `public/rampcnn/frames/` remain independent and are not deleted by keeping or viewing this backup branch.
