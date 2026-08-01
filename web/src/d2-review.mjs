import {
  createD2ReviewSurfaces,
  D2_REVIEW_SURFACE_IDS,
} from "./d2-review-model.mjs";
import { renderD2Surface } from "./d2-review-view.mjs";

const requested = new URLSearchParams(window.location.search).get("surface");
const activeId = D2_REVIEW_SURFACE_IDS.includes(requested) ? requested : "r1";
const surfaces = createD2ReviewSurfaces();
const active = surfaces[activeId];

document.querySelector("#reviewSurface").innerHTML = renderD2Surface(active);
document.querySelector("#surfaceCounter").textContent = `SURFACE ${active.index} / 04`;
document.title = `NIGHTMARE LAB · ${activeId.toUpperCase()} ${active.eyebrow.split(" / ")[0]}`;

document.querySelectorAll("[data-surface-link]").forEach((link) => {
  if (link.dataset.surfaceLink === activeId) link.setAttribute("aria-current", "page");
});
