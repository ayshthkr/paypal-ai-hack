"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";

const stops = ["Portfolio", "Verdant Market outlier", "Action queue", "Model performance", "Reply triage"];
const ACTIVE_KEY = "latepayer-tour-active";
const STOP_KEY = "latepayer-tour-stop";
const CHANGE_EVENT = "latepayer-tour-change";

function subscribe(callback: () => void) {
  window.addEventListener(CHANGE_EVENT, callback);
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(CHANGE_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}
function readActive() { return window.sessionStorage.getItem(ACTIVE_KEY) === "1"; }
function readStop() { return Number(window.sessionStorage.getItem(STOP_KEY) || "1"); }
function serverActive() { return false; }
function serverStop() { return 1; }
function notifyTourChange() { window.dispatchEvent(new Event(CHANGE_EVENT)); }

export default function TourNavigation() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const searchKey = searchParams.toString();
  const storedActive = useSyncExternalStore(subscribe, readActive, serverActive);
  const storedStop = useSyncExternalStore(subscribe, readStop, serverStop);
  const isTourPage = pathname === "/tour";
  const explicitlyActive = isTourPage || searchParams.get("tour") === "1";
  const active = explicitlyActive || storedActive;
  const routeStop = Number(searchParams.get("tour_stop") || (isTourPage ? searchParams.get("stop") : ""));
  const stopValue = Number.isFinite(routeStop) && routeStop > 0 ? routeStop : storedStop;
  const stop = Math.max(1, Math.min(stops.length, stopValue));

  useEffect(() => {
    if (!explicitlyActive) return;
    sessionStorage.setItem(ACTIVE_KEY, "1");
    if (Number.isFinite(routeStop) && routeStop > 0) sessionStorage.setItem(STOP_KEY, String(stop));
    notifyTourChange();
  }, [explicitlyActive, routeStop, searchKey, stop]);

  if (!active) return null;
  const previous = Math.max(1, stop - 1);
  const next = Math.min(stops.length, stop + 1);
  return <div className="tour-return-bar" role="region" aria-label="Guided tour navigation">
    <span>Tour stop {stop} of {stops.length} · {stops[stop - 1]}</span>
    <nav aria-label="Continue guided tour">
      <Link className="btn" href={`/tour?stop=${previous}`}>← Previous</Link>
      <Link className="btn primary" href={`/tour?stop=${next}`}>{stop === stops.length ? "Return to tour" : "Continue tour →"}</Link>
      <Link className="btn" href="/" onClick={() => { sessionStorage.removeItem(ACTIVE_KEY); notifyTourChange(); }}>Exit tour</Link>
    </nav>
  </div>;
}
