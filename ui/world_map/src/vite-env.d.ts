/// <reference types="vite/client" />

declare module "react-globe.gl" {
  import type { ForwardedRef } from "react";

  export type GlobeMethods = {
    controls: () => {
      autoRotate: boolean;
      autoRotateSpeed: number;
      enableDamping: boolean;
    };
    pointOfView: (
      position?: { lat: number; lng: number; altitude?: number },
      transitionMs?: number,
    ) => { lat: number; lng: number; altitude: number };
    getScreenCoords: (lat: number, lng: number, altitude?: number) => { x: number; y: number };
  };

  export type GlobeProps = {
    ref?: ForwardedRef<GlobeMethods>;
    width?: number;
    height?: number;
    backgroundColor?: string;
    globeImageUrl?: string;
    bumpImageUrl?: string;
    showAtmosphere?: boolean;
    atmosphereColor?: string;
    atmosphereAltitude?: number;
    polygonsData?: unknown[];
    polygonCapColor?: string | ((item: unknown) => string);
    polygonSideColor?: string | ((item: unknown) => string);
    polygonStrokeColor?: string | ((item: unknown) => string);
    polygonAltitude?: number | ((item: unknown) => number);
    onPolygonClick?: (item: unknown) => void;
    onPolygonHover?: (item: unknown | null) => void;
    pointsData?: unknown[];
    labelsData?: unknown[];
    onGlobeReady?: () => void;
  };

  export default function Globe(props: GlobeProps): JSX.Element;
}
