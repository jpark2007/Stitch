"use client";

/**
 * Optional depth of field for the standalone stage (DESIGN.md: "a
 * depth-of-field feel"). Whatever the camera rig looks at stays sharp; nearer
 * and farther layers soften a little, which sells the miniature on a table
 * when the camera dollies in. Off by default (it costs a depth pre-pass and a
 * full-screen blur); the render route turns it on with ?dof.
 *
 * Built from three's own BokehPass, no extra dependency. It takes over
 * rendering for the Canvas (useFrame priority 1): scene -> MSAA half-float
 * target -> bokeh -> tone mapping + sRGB output. The focus distance is the
 * one <CameraRig> writes to `camera.userData.focus` every frame.
 */

import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import { HalfFloatType, WebGLRenderTarget, type Camera, type Scene, type WebGLRenderer } from "three";
import { BokehPass } from "three/examples/jsm/postprocessing/BokehPass.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";

/**
 * BokehPass, adjusted for the stage:
 *  - the scene background is hidden during its depth pre-pass (it would be
 *    drawn into the depth target as colour and read back as depth);
 *  - shadow maps are not redrawn for the pre-pass (the main render already
 *    drew them this frame);
 *  - the depth target is half resolution (the blur is smooth anyway).
 */
class PaperBokehPass extends BokehPass {
  setFocus(distance: number) {
    this.materialBokeh.uniforms.focus.value = distance;
  }
  render(...args: Parameters<BokehPass["render"]>) {
    const renderer = args[0];
    const background = this.scene.background;
    const autoShadows = renderer.shadowMap.autoUpdate;
    this.scene.background = null;
    renderer.shadowMap.autoUpdate = false;
    super.render(...args);
    renderer.shadowMap.autoUpdate = autoShadows;
    this.scene.background = background;
  }
  setSize(width: number, height: number) {
    super.setSize(width, height);
    const depth = (this as unknown as { _renderTargetDepth?: WebGLRenderTarget })._renderTargetDepth;
    depth?.setSize(Math.max(1, width >> 1), Math.max(1, height >> 1));
  }
}

export interface DepthOfFieldProps {
  /** Blur per unit of distance from the focus plane. Default 0.0018. */
  aperture?: number;
  /** Largest blur (fraction of the frame). Default 0.006. */
  maxblur?: number;
}

function buildComposer(gl: WebGLRenderer, scene: Scene, camera: Camera, aperture: number, maxblur: number) {
  const target = new WebGLRenderTarget(1, 1, { type: HalfFloatType, samples: 4 });
  const composer = new EffectComposer(gl, target);
  composer.addPass(new RenderPass(scene, camera));
  const bokeh = new PaperBokehPass(scene, camera, { focus: 6, aperture, maxblur });
  composer.addPass(bokeh);
  composer.addPass(new OutputPass());
  return { composer, bokeh };
}

export function DepthOfField({ aperture = 0.0018, maxblur = 0.006 }: DepthOfFieldProps) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const size = useThree((s) => s.size);
  const dpr = useThree((s) => s.viewport.dpr);

  const fx = useMemo(() => buildComposer(gl, scene, camera, aperture, maxblur), [gl, scene, camera, aperture, maxblur]);
  useEffect(() => () => fx.composer.dispose(), [fx]);
  useEffect(() => {
    // The blur hides the last bit of resolution: cap the composer's pixel
    // ratio so the extra passes stay cheap on retina screens.
    fx.composer.setPixelRatio(Math.min(dpr, 1.25));
    fx.composer.setSize(size.width, size.height);
  }, [fx, size.width, size.height, dpr]);

  useFrame((state, delta) => {
    const focus = state.camera.userData.focus;
    fx.bokeh.setFocus(typeof focus === "number" ? focus : 6);
    fx.composer.render(delta);
  }, 1);

  return null;
}
