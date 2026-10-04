import L from "leaflet";

/**
 * An image pinned to the map by three corners (top-left, top-right,
 * bottom-left). Any 3 points give move + scale + rotate (+ skew), which is
 * what lining a site-plan photo up with the real ground needs. Positioned
 * with a CSS matrix in the overlay pane; hidden during zoom animation and
 * re-placed after.
 */
export type Corners = { tl: L.LatLng; tr: L.LatLng; bl: L.LatLng };

export class AffineImage extends L.Layer {
  private img: HTMLImageElement;
  private corners: Corners;
  private w = 1;
  private h = 1;
  private paneName: string;

  constructor(url: string, corners: Corners, opacity = 0.85, pane = "overlayPane") {
    super();
    this.paneName = pane;
    this.corners = corners;
    this.img = L.DomUtil.create("img", "geo-bg-img") as HTMLImageElement;
    this.img.style.position = "absolute";
    this.img.style.left = "0";
    this.img.style.top = "0";
    this.img.style.transformOrigin = "0 0";
    this.img.style.pointerEvents = "none";
    this.img.style.maxWidth = "none";
    this.img.style.opacity = String(opacity);
    this.img.onload = () => {
      this.w = this.img.naturalWidth || 1;
      this.h = this.img.naturalHeight || 1;
      this.place();
    };
    this.img.src = url;
  }

  onAdd(map: L.Map): this {
    (map.getPane(this.paneName) ?? map.getPanes().overlayPane).appendChild(this.img);
    map.on("zoomend viewreset moveend", this.place, this);
    map.on("zoomstart", this.hide, this);
    this.place();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off("zoomend viewreset moveend", this.place, this);
    map.off("zoomstart", this.hide, this);
    this.img.remove();
    return this;
  }

  naturalSize(): [number, number] { return [this.w, this.h]; }
  getCorners(): Corners { return this.corners; }
  setCorners(c: Corners): void { this.corners = c; this.place(); }
  setOpacity(o: number): void { this.img.style.opacity = String(o); }
  setUrl(u: string): void { this.img.src = u; }

  private hide(): void { this.img.style.visibility = "hidden"; }

  private place(): void {
    const map = (this as unknown as { _map?: L.Map })._map;
    if (!map) return;
    const tl = map.latLngToLayerPoint(this.corners.tl);
    const tr = map.latLngToLayerPoint(this.corners.tr);
    const bl = map.latLngToLayerPoint(this.corners.bl);
    const a = (tr.x - tl.x) / this.w, b = (tr.y - tl.y) / this.w;
    const c = (bl.x - tl.x) / this.h, d = (bl.y - tl.y) / this.h;
    this.img.style.width = `${this.w}px`;
    this.img.style.height = `${this.h}px`;
    this.img.style.transform = `matrix(${a}, ${b}, ${c}, ${d}, ${tl.x}, ${tl.y})`;
    this.img.style.visibility = "visible";
  }
}

/** Downscale big photos client-side so uploads stay small and fast. */
export async function shrinkImage(file: File, maxDim = 4096): Promise<Blob> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = rej;
      i.src = url;
    });
    const s = Math.min(1, maxDim / Math.max(img.naturalWidth, img.naturalHeight));
    if (s === 1 && file.size < 6 * 1024 * 1024) return file;
    const cv = document.createElement("canvas");
    cv.width = Math.round(img.naturalWidth * s);
    cv.height = Math.round(img.naturalHeight * s);
    cv.getContext("2d")!.drawImage(img, 0, 0, cv.width, cv.height);
    const png = file.type === "image/png";
    return await new Promise<Blob>((res) => cv.toBlob((b) => res(b!), png ? "image/png" : "image/jpeg", 0.88));
  } finally {
    URL.revokeObjectURL(url);
  }
}
