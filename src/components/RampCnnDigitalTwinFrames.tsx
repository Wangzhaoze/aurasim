import { useMemo, useRef, useState } from "react";
import {
  RAMP_CNN_SCENES,
  padRampCnnFrame,
  rampCnnFrameUrl,
  type RampCnnScene,
} from "./rampCnnFrameConfig";

type CompareMode = "split" | "blend";

function FrameImage({ src, alt }: { src: string; alt: string }) {
  const [missing, setMissing] = useState(false);

  return (
    <>
      <img
        src={src}
        alt={alt}
        draggable={false}
        onLoad={() => setMissing(false)}
        onError={() => setMissing(true)}
        className="absolute inset-0 h-full w-full select-none object-contain"
      />
      {missing && (
        <div className="absolute inset-0 grid place-items-center bg-zinc-100/95 px-4 text-center text-xs text-zinc-500 dark:bg-zinc-950/95 dark:text-zinc-400">
          Upload frame data to
          <br />
          <code className="mt-1 break-all text-[10px]">{src}</code>
        </div>
      )}
    </>
  );
}

function SceneCard({ scene }: { scene: RampCnnScene }) {
  const [frame, setFrame] = useState(1);
  const [mode, setMode] = useState<CompareMode>("split");
  const [mix, setMix] = useState(50);
  const stageRef = useRef<HTMLDivElement>(null);

  const realSrc = useMemo(
    () => rampCnnFrameUrl(scene.id, "camera/real", frame),
    [scene.id, frame],
  );
  const simSrc = useMemo(
    () => rampCnnFrameUrl(scene.id, "camera/sim", frame),
    [scene.id, frame],
  );

  const updateFromPointer = (clientX: number) => {
    if (mode !== "split" || !stageRef.current) return;
    const rect = stageRef.current.getBoundingClientRect();
    const value = Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100));
    setMix(Math.round(value));
  };

  return (
    <article className="overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex items-end justify-between gap-3 border-b border-zinc-200 px-3.5 py-3 dark:border-zinc-700">
        <div>
          <div className="font-semibold text-zinc-900 dark:text-zinc-100">{scene.label}</div>
          <div className="text-xs text-zinc-500 dark:text-zinc-400">{scene.subtitle}</div>
        </div>
        <div className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
          {padRampCnnFrame(frame)} / {scene.frames}
        </div>
      </div>

      <div className="flex gap-2 px-3.5 pt-3">
        {(["split", "blend"] as CompareMode[]).map((item) => (
          <button
            key={item}
            type="button"
            onClick={() => setMode(item)}
            className={`rounded-md border px-2.5 py-1 text-xs font-medium transition ${
              mode === item
                ? "border-cyan-500 bg-cyan-600 text-white"
                : "border-zinc-300 bg-zinc-50 text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
            }`}
          >
            {item === "split" ? "Split" : "Blend"}
          </button>
        ))}
      </div>

      <div
        ref={stageRef}
        className={`relative mx-3.5 mt-3 aspect-[4/3] overflow-hidden rounded-lg bg-zinc-100 dark:bg-zinc-950 ${
          mode === "split" ? "cursor-ew-resize touch-none" : ""
        }`}
        onPointerDown={(event) => {
          if (mode !== "split") return;
          event.currentTarget.setPointerCapture(event.pointerId);
          updateFromPointer(event.clientX);
        }}
        onPointerMove={(event) => {
          if (mode !== "split" || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
          updateFromPointer(event.clientX);
        }}
      >
        <FrameImage src={realSrc} alt={`${scene.label} real camera`} />
        <div
          className="absolute inset-0"
          style={
            mode === "split"
              ? { clipPath: `inset(0 ${100 - mix}% 0 0)` }
              : { opacity: mix / 100 }
          }
        >
          <FrameImage src={simSrc} alt={`${scene.label} AuRaSim camera`} />
        </div>

        {mode === "split" && (
          <div
            className="pointer-events-none absolute bottom-0 top-0 w-0.5 bg-white shadow-[0_0_8px_rgba(0,0,0,.75)]"
            style={{ left: `${mix}%` }}
          >
            <div className="absolute left-1/2 top-1/2 grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white bg-zinc-900/75 text-xs text-white">
              ↔
            </div>
          </div>
        )}

        <div className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white">
          AuRaSim
        </div>
        <div className="pointer-events-none absolute right-2 top-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white">
          Real
        </div>
      </div>

      <div className="px-3.5 pb-3.5 pt-3">
        <div className="mb-3 grid grid-cols-[1fr_auto] items-center gap-2">
          <input
            aria-label={`${scene.label} ${mode} ratio`}
            type="range"
            min={0}
            max={100}
            value={mix}
            onChange={(event) => setMix(Number(event.target.value))}
            className="w-full accent-cyan-600"
          />
          <span className="w-10 text-right font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
            {mix}%
          </span>
        </div>
        <div className="grid grid-cols-[1fr_auto] items-center gap-2">
          <input
            aria-label={`${scene.label} frame`}
            type="range"
            min={1}
            max={scene.frames}
            value={frame}
            onChange={(event) => setFrame(Number(event.target.value))}
            className="w-full accent-cyan-600"
          />
          <span className="min-w-[5.5rem] text-right font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
            Frame {padRampCnnFrame(frame)}
          </span>
        </div>
      </div>
    </article>
  );
}

export default function RampCnnDigitalTwinFrames() {
  return (
    <div className="not-prose my-6 grid gap-4 lg:grid-cols-3">
      {RAMP_CNN_SCENES.map((scene) => (
        <SceneCard key={scene.id} scene={scene} />
      ))}
    </div>
  );
}
