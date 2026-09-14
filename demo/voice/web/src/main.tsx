import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

// The page follows the viewer's theme; the palette is defined for both in index.css.
if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
