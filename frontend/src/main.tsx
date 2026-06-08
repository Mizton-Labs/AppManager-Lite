import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { applyFavicon } from "./branding";
import "./styles.css";

/**
 * Derive the router basename from the document base URI. The backend injects a
 * `<base href>` matching the deployment prefix, so the History API uses the
 * correct absolute paths under any mount point (e.g. "/home").
 */
function routerBasename(): string {
  const path = new URL(document.baseURI).pathname;
  return path.replace(/\/+$/, "") || "/";
}

applyFavicon();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter basename={routerBasename()}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
