# RAMP-CNN web frame layout

The AuRaSim project page now reads all Digital Twin and Sim2Real frame data from this public directory.

Upload the same synchronized frame index to four folders per scene. The website expects **WebP** files named exactly `frame_XXXX.webp`, starting from `frame_0001.webp`.

```text
public/rampcnn/frames/
├─ bms1000/
│  ├─ camera/
│  │  ├─ real/
│  │  │  ├─ frame_0001.webp
│  │  │  ├─ frame_0002.webp
│  │  │  └─ ...
│  │  └─ sim/
│  │     ├─ frame_0001.webp
│  │     ├─ frame_0002.webp
│  │     └─ ...
│  └─ radar/
│     ├─ real/
│     │  ├─ frame_0001.webp
│     │  ├─ frame_0002.webp
│     │  └─ ...
│     └─ sim/
│        ├─ frame_0001.webp
│        ├─ frame_0002.webp
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

## Recommended web export

Use WebP rather than raw PNG sequences when possible. Thousands of full-resolution PNG frames will make the repository and GitHub Pages deployment unnecessarily large. A practical target is roughly 960–1280 px on the long side with visually lossless/high-quality WebP compression.
