import { get } from "./api.js";

// Shared state: the project list (names for slugs everywhere) and a tiny event bus so a change made in
// the detail drawer updates every card that shows the same item.

let projects = [];
let projectsPromise = null;

export async function loadProjects(force = false) {
  if (force || !projectsPromise) {
    projectsPromise = get("/projects").then((list) => (projects = list)).catch((error) => {
      projectsPromise = null;
      throw error;
    });
  }
  return projectsPromise;
}

export const getProjects = () => projects;
export const projectBySlug = (slug) => projects.find((p) => p.slug === slug);
export const projectName = (slug) => projectBySlug(slug)?.name || slug;

export const bus = new EventTarget();
export const emit = (type, detail) => bus.dispatchEvent(new CustomEvent(type, { detail }));
export const on = (type, handler) => {
  const listener = (event) => handler(event.detail);
  bus.addEventListener(type, listener);
  return () => bus.removeEventListener(type, listener);
};

// Live AI state for the header pill and the home screen: {llm_enabled, ai_gateway: {ready, code} | null}.
export const health = { value: null };
