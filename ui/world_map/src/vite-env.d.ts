/// <reference types="vite/client" />

declare module "react-globe.gl" {
  import type { ForwardRefExoticComponent, RefAttributes } from "react";

  type Accessor<T, R> = R | ((item: T) => R);

  export interface GlobeMethods {
    controls: () => {
      autoRotate: boolean;
      autoRotateSpeed: number;
      enableDamping: boolean;
    };
    pointOfView: (
      position: { lat?: number; lng?: number; altitude?: number },
      transitionMs?: number,
    ) => void;
  }

  export interface GlobeProps<TPoint = unknown, TPolygon = unknown, TLabel = unknown> {
    width?: number;
    height?: number;
    backgroundColor?: string;
    globeImageUrl?: string;
    bumpImageUrl?: string;
    showAtmosphere?: boolean;
    atmosphereColor?: string;
    atmosphereAltitude?: number;
    polygonsData?: TPolygon[];
    polygonCapColor?: Accessor<TPolygon, string>;
    polygonSideColor?: Accessor<TPolygon, string>;
    polygonStrokeColor?: Accessor<TPolygon, string>;
    polygonAltitude?: Accessor<TPolygon, number>;
    onPolygonClick?: (polygon: TPolygon) => void;
    onPolygonHover?: (polygon: TPolygon | null) => void;
    pointsData?: TPoint[];
    pointLat?: Accessor<TPoint, number>;
    pointLng?: Accessor<TPoint, number>;
    pointAltitude?: Accessor<TPoint, number>;
    pointRadius?: Accessor<TPoint, number>;
    pointColor?: Accessor<TPoint, string>;
    pointLabel?: Accessor<TPoint, string>;
    onPointClick?: (point: TPoint) => void;
    labelsData?: TLabel[];
    labelLat?: Accessor<TLabel, number>;
    labelLng?: Accessor<TLabel, number>;
    labelText?: Accessor<TLabel, string>;
    labelSize?: Accessor<TLabel, number>;
    labelColor?: Accessor<TLabel, string>;
    labelDotRadius?: Accessor<TLabel, number>;
    onGlobeReady?: () => void;
  }

  const Globe: ForwardRefExoticComponent<GlobeProps & RefAttributes<GlobeMethods>>;
  export default Globe;
}
