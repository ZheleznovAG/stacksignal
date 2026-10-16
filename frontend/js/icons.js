// Stroke icons on a 24px grid (Lucide-style), drawn inline so the dashboard needs no icon font or CDN.

const PATHS = {
  logo: '<circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/><path d="M7.5 16.5a6.4 6.4 0 0 1 0-9"/><path d="M16.5 7.5a6.4 6.4 0 0 1 0 9"/><path d="M4.4 19.6a10.7 10.7 0 0 1 0-15.2"/><path d="M19.6 4.4a10.7 10.7 0 0 1 0 15.2"/>',
  today: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9v11.5h13V9"/><path d="M10 20.5v-6h4v6"/>',
  projects: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
  explore: '<circle cx="12" cy="12" r="9"/><path d="m15.8 8.2-2.4 5.2-5.2 2.4 2.4-5.2z"/>',
  chat: '<path d="M20.5 12a8.5 8.5 0 0 1-12.2 7.7L3.5 21l1.3-4.4A8.5 8.5 0 1 1 20.5 12z"/>',
  settings: '<path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="17" r="2"/>',
  play: '<path d="M7 4.5v15l12.5-7.5z"/>',
  refresh: '<path d="M20 11.5A8 8 0 0 0 6 6.3L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M4 12.5a8 8 0 0 0 14 5.2l2-2.2"/><path d="M20 20v-4.5h-4.5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6 6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.6A8.2 8.2 0 1 1 9.4 4a6.6 6.6 0 0 0 10.6 10.6z"/>',
  monitor: '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M8.5 20h7M12 16.5V20"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
  up: '<path d="M7 11v9.5H4.5a1 1 0 0 1-1-1V12a1 1 0 0 1 1-1z"/><path d="M7 11l3.6-7.3a2 2 0 0 1 3.4 1.7L13.4 9h5.4a2 2 0 0 1 2 2.4l-1.5 7.4a2 2 0 0 1-2 1.7H7"/>',
  down: '<path d="M17 13V3.5h2.5a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1z"/><path d="M17 13l-3.6 7.3a2 2 0 0 1-3.4-1.7l.6-3.6H5.2a2 2 0 0 1-2-2.4l1.5-7.4a2 2 0 0 1 2-1.7H17"/>',
  note: '<path d="M5 3.5h10l4 4v13H5z"/><path d="M14.5 3.5V8H19"/><path d="M8.5 12.5h7M8.5 16h4.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<path d="M10.3 4.3 2.8 17.5A2 2 0 0 0 4.5 20.5h15a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/>',
  sparkle: '<path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7-5.6-1.9L10.1 9z"/><path d="M19 3.5v3M17.5 5h3"/>',
  star: '<path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>',
  download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 20h14"/>',
  fork: '<circle cx="6.5" cy="5.5" r="2"/><circle cx="17.5" cy="5.5" r="2"/><circle cx="12" cy="18.5" r="2"/><path d="M6.5 7.5v1.5a3 3 0 0 0 3 3h5a3 3 0 0 0 3-3V7.5M12 12v4.5"/>',
  heart: '<path d="M12 20s-7.5-4.6-7.5-10A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 7.5 3c0 5.4-7.5 10-7.5 10z"/>',
  tag: '<path d="M3.5 12.3V4.5a1 1 0 0 1 1-1h7.8l8.2 8.2a1.5 1.5 0 0 1 0 2.1l-6.6 6.6a1.5 1.5 0 0 1-2.1 0z"/><circle cx="8" cy="8" r="1.4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.4-4.4"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  back: '<path d="m15 6-6 6 6 6"/>',
  trash: '<path d="M4.5 7h15M10 11v6M14 11v6M6.5 7l.9 12.1a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4L17.5 7M9.5 7V4.5h5V7"/>',
  edit: '<path d="M4 20h4.2L19.4 8.8a2 2 0 0 0 0-2.8l-1.4-1.4a2 2 0 0 0-2.8 0L4 15.8z"/><path d="m13.5 6.5 4 4"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l2.8-2.8a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-2.8 2.8a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
  send: '<path d="M4 11.5 20.5 4l-7.5 16.5-2.4-6.6z"/><path d="m10.6 13.9 4-4"/>',
  package: '<path d="M3.5 7.5 12 3.2l8.5 4.3v9L12 20.8l-8.5-4.3z"/><path d="M3.5 7.5 12 11.8l8.5-4.3M12 11.8v9"/>',
  paper: '<path d="M6 3.5h8.5l4 4v13H6z"/><path d="M14 3.5V8h4.5"/>',
  cube: '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M12 12 20 7.5M12 12 4 7.5M12 12v9"/>',
  inbox: '<path d="M3.5 13.5h5l1.5 2.5h4l1.5-2.5h5"/><path d="M6 5h12l2.5 8.5V19a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1v-5.5z"/>',
  bolt: '<path d="M13 2.5 4.5 14h6.5l-1 7.5L18.5 10H12z"/>',
  layers: '<path d="m12 3.5 9 4.5-9 4.5-9-4.5z"/><path d="m3 12 9 4.5 9-4.5"/><path d="m3 16 9 4.5 9-4.5"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12 18.4 5.6"/><circle cx="12" cy="12" r="1" fill="currentColor"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>',
  telegram: '<path d="M21 4.5 2.8 11.4a.8.8 0 0 0 .1 1.5l4.5 1.4 1.7 5.3a.8.8 0 0 0 1.3.3l2.6-2.4 4.5 3.3a.8.8 0 0 0 1.3-.5L21.9 5.4a.8.8 0 0 0-.9-.9z"/><path d="m7.4 14.3 9.6-6.3-7.3 7.1"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
  history: '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M12 8v4l3 2"/>',
  swap: '<path d="M7 7h13l-3.5-3.5M17 17H4l3.5 3.5"/>',
  git: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 8.2v7.6M18 11.2c0 3.8-4 4.3-9.8 5.3"/>',
  hf: '<circle cx="12" cy="12" r="9"/><path d="M8.5 10.5h.01M15.5 10.5h.01"/><path d="M8.5 14.5a4 4 0 0 0 7 0"/>',
};

export function icon(name, { size = 16, label } = {}) {
  const wrapper = document.createElement("span");
  wrapper.innerHTML =
    `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"${label ? ` role="img" aria-label="${label}"` : ' aria-hidden="true"'}>` +
    `${PATHS[name] || ""}</svg>`;
  return wrapper.firstElementChild;
}

export const SOURCE_ICON = { github: "git", huggingface: "hf", arxiv: "paper", npm: "package", pypi: "package" };
