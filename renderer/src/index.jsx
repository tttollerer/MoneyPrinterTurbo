import React from "react";
import { Composition, registerRoot } from "remotion";
import { VideoComposition } from "./Composition.jsx";
function Root() {
  return (
    <Composition
      id="SpotForge"
      component={VideoComposition}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={150}
      calculateMetadata={({ props }) => ({
        width: props.manifest.format.width,
        height: props.manifest.format.height,
        fps: props.manifest.format.fps,
        durationInFrames: props.manifest.duration_frames,
      })}
    />
  );
}
registerRoot(Root);
