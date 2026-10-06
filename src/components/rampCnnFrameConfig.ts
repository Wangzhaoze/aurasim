export type RampCnnSceneId = "bms1000" | "pms2000" | "mlms001";
export type RampCnnFrameKind =
  | "camera/real"
  | "camera/sim"
  | "radar/real"
  | "radar/sim";

export interface RampCnnScene {
  id: RampCnnSceneId;
  label: string;
  subtitle: string;
  frames: number;
}

// Keep all website frame sequences under one canonical public directory.
// Digital Twin uses camera/real + camera/sim from the same data that Sim2Real uses,
// so those images only need to be uploaded once.
export const RAMP_CNN_FRAME_ROOT = "rampcnn/frames";

// Use lossless PNG for the web frame sequences. Repository size is acceptable here;
// runtime smoothness will be handled by browser caching/prefetch rather than lossy image compression.
export const RAMP_CNN_FRAME_EXTENSION = "png";

// The synchronized frame counts use the common Camera/Radar interval from the
// current RAMP-CNN dashboard. If a newly regenerated sequence has a different
// number of matched frames, only these three numbers need to change.
export const RAMP_CNN_SCENES: RampCnnScene[] = [
  { id: "bms1000", label: "BMS1000", subtitle: "Parking Lot", frames: 896 },
  { id: "pms2000", label: "PMS2000", subtitle: "Parking Lot", frames: 894 },
  { id: "mlms001", label: "MLMS001", subtitle: "Campus Road", frames: 898 },
];

export const padRampCnnFrame = (frame: number) => String(frame).padStart(4, "0");

export function rampCnnFrameUrl(
  scene: RampCnnSceneId,
  kind: RampCnnFrameKind,
  frame: number,
): string {
  const base = import.meta.env.BASE_URL.endsWith("/")
    ? import.meta.env.BASE_URL
    : `${import.meta.env.BASE_URL}/`;

  return `${base}${RAMP_CNN_FRAME_ROOT}/${scene}/${kind}/frame_${padRampCnnFrame(frame)}.${RAMP_CNN_FRAME_EXTENSION}`;
}
