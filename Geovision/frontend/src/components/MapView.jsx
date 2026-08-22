import { useEffect } from "react";
import L from "leaflet";
import {
  MapContainer,
  TileLayer,
  ImageOverlay,
  useMap,
  useMapEvents,
} from "react-leaflet";
import { Crosshair, Stack } from "@phosphor-icons/react";
import { PhotoLayer } from "./PhotoLayer.jsx";

/**
 * Pan/zoom to the photo-point cluster when it changes and no analysis
 * result is on screen — otherwise fresh uploads open off-screen (Leaflet
 * culls out-of-view circles) and users can't tell their photos landed.
 * An active analysis view always wins.
 */
function PhotoFocus({ photos, disabled }) {
  const map = useMap();
  useEffect(() => {
    if (disabled || !Array.isArray(photos) || photos.length === 0) return;
    const b = L.latLngBounds(photos.map((p) => [p.lat, p.lon]));
    map.fitBounds(b.pad(0.35), { padding: [28, 28] });
    map.invalidateSize();
  }, [map, photos, disabled]);
  return null;
}

const DARK_ATTRIBUTION =
  '&copy; <a href="https://www.esri.com">Esri</a> &copy; OpenStreetMap contributors';

function BoundsFitter({ bounds }) {
  const map = useMap();
  useEffect(() => {
    if (bounds) {
      map.fitBounds(bounds, { padding: [28, 28] });
      map.invalidateSize();
    }
  }, [map, bounds]);
  return null;
}

function ClickCatcher({ onAoiClick }) {
  useMapEvents({
    click(e) {
      onAoiClick?.(e.latlng);
    },
  });
  return null;
}

function MapView({ result, view, onViewChange, showMask, onToggleMask, onAoiClick, photos }) {
  const bounds = result ? latLngBounds(result.aoi_bounds) : DEFAULT_BOUNDS;

  return (
    <div className="map-wrap">
      <MapContainer
        center={CENTER}
        zoom={9}
        className="map"
        zoomControl={false}
        attributionControl={false}
      >
        <TileLayer url={TILE_URL} attribution={DARK_ATTRIBUTION} maxZoom={18} />
        <ClickCatcher onAoiClick={onAoiClick} />

        {result && (
          <>
            {view === "before" && (
              <ImageOverlay url={result.before_thumbnail_url} bounds={bounds} />
            )}
            {view === "after" && (
              <ImageOverlay url={result.after_thumbnail_url} bounds={bounds} />
            )}
            {showMask && (
              <ImageOverlay url={result.mask_thumbnail_url} bounds={bounds} opacity={0.7} />
            )}
          </>
        )}

        <PhotoLayer photos={photos} />
        <PhotoFocus photos={photos} disabled={!!result} />

        <BoundsFitter bounds={bounds} />
      </MapContainer>

      {result && (
        <div className="map-overlay">
          <div className="seg-control">
            <button
              type="button"
              className={view === "before" ? "is-active" : ""}
              onClick={() => onViewChange("before")}
            >
              Before
            </button>
            <button
              type="button"
              className={view === "after" ? "is-active" : ""}
              onClick={() => onViewChange("after")}
            >
              After
            </button>
          </div>

          <label className="mask-toggle">
            <input
              type="checkbox"
              checked={showMask}
              onChange={(e) => onToggleMask(e.target.checked)}
            />
            <Stack size={14} weight="duotone" />
            Change mask
          </label>
        </div>
      )}

      <div className="map-legend">
        <Crosshair size={13} weight="duotone" />
        Click map to set a custom AOI
      </div>
    </div>
  );
}

function latLngBounds(bbox) {
  const [w, s, e, n] = bbox;
  return L.latLngBounds([[s, w], [n, e]]);
}

const CENTER = [28.1, 68.4];
const DEFAULT_BOUNDS = L.latLngBounds([[27.9, 68.2], [28.3, 68.6]]);
const TILE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export { MapView };
