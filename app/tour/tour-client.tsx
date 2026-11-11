"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { requestJson } from "../lib/api";

type DemoStory = { key: string; title: string; summary: string; href: string };

const steps = [
  {
    title: "Portfolio",
    read: "Open balance, past-due invoices, and current client timing signals.",
    detail: "Start with cash exposure and today’s review actions. The forecast distributes expected cash by settlement probability.",
    href: "/",
  },
  {
    title: "Verdant Market outlier",
    read: "An unpaid invoice’s overdue age is a lower bound, not a predicted payment date.",
    detail: "Inspect the invoice forecast, right-censored client history, and the policy reason before describing this stale balance.",
    story: "verdant_outlier",
  },
  {
    title: "Action queue",
    read: "Every suggestion has a forecast reason and a human approval step.",
    detail: "Review an internal reminder note, recorded payment outcomes, and the collections workflow. Synthetic approvals are simulations.",
    href: "/actions",
  },
  {
    title: "Model performance",
    read: "The release model is named against its validation selection rule and held-out results.",
    detail: "Show censor-aware scores and calibration. Synthetic results are pipeline evidence, not real-world accuracy claims.",
    href: "/performance",
  },
  {
    title: "Reply triage",
    read: "A reviewed promise changes the forecast as a labeled scenario; an approved dispute pauses reminders.",
    detail: "Inspect the reply preview, correct its type or date, and approve it before invoice state changes.",
    story: "dispute",
  },
];

const STORY_FALLBACK: Record<string, string> = { verdant_outlier: "/offline-demo?stop=2", dispute: "/offline-demo?stop=5" };

export default function TourClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchKey = searchParams.toString();
  const fromUrl = Number(searchParams.get("stop") || "1");
  const activeIndex = Math.max(0, Math.min(steps.length - 1, (Number.isFinite(fromUrl) ? fromUrl : 1) - 1));
  const [stories, setStories] = useState<DemoStory[]>([]);
  const [storyError, setStoryError] = useState("");
  const [retry, setRetry] = useState(0);

  const setStop = useCallback((index: number) => {
    const next = new URLSearchParams(searchKey);
    next.set("stop", String(index + 1));
    const query = next.toString();
    sessionStorage.setItem("latepayer-tour-stop", String(index + 1));
    sessionStorage.setItem("latepayer-tour-active", "1");
    window.dispatchEvent(new Event("latepayer-tour-change"));
    router.replace(`/tour?${query}`, { scroll: false });
  }, [router, searchKey]);

  useEffect(() => {
    if (searchParams.has("stop")) return;
    const saved = Number(sessionStorage.getItem("latepayer-tour-stop") || "1");
    if (saved > 1 && saved <= steps.length) {
      const next = new URLSearchParams(searchKey);
      next.set("stop", String(saved));
      router.replace(`/tour?${next.toString()}`, { scroll: false });
    }
  }, [router, searchKey, searchParams]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setStoryError("");
      let last: unknown = null;
      for (let attempt = 0; attempt < 6; attempt += 1) {
        try {
          const result = await requestJson<{ items: DemoStory[] }>("/api/demo-stories", { signal: AbortSignal.timeout(30_000) });
          if (active) setStories(result.items);
          return;
        } catch (cause) {
          last = cause;
          if (attempt < 5) await new Promise(resolve => window.setTimeout(resolve, 1_500 + attempt * 500));
        }
      }
      if (active) setStoryError(last instanceof Error ? last.message : "Named stories are unavailable. Use the local fallback stops or retry.");
    };
    void load();
    return () => { active = false; };
  }, [retry]);

  const step = steps[activeIndex];
  const story = step.story ? stories.find(item => item.key === step.story) : null;
  const href = story?.href || step.href || STORY_FALLBACK[step.story || ""] || "/offline-demo";
  const anchor = step.story === "dispute" ? "#reply-triage" : "";
  const destination = new URL(href, "https://latepayer.invalid");
  destination.searchParams.set("tour", "1");
  destination.searchParams.set("tour_stop", String(activeIndex + 1));
  const openHref = `${destination.pathname}${destination.search}${anchor}`;
  return <>
    <div className="heading-row"><div><div className="eyebrow">Three-minute route · five stops</div><h1>Guided demo tour</h1><p className="subheading">Use these five screens to explain the receivables workflow, its forecast limits, and the human review step.</p></div></div>
    {storyError && <div className="empty-inline" role="status">The API did not load the named story after retries. This stop still has a local fallback. <button className="btn" onClick={() => setRetry(value => value + 1)}>Retry stories</button></div>}
    <section className="panel guided-stop" aria-label={`Stop ${activeIndex + 1} of 5`}>
      <div className="eyebrow">Stop {activeIndex + 1} of 5 · about 35 seconds</div>
      <h2>{step.title}</h2>
      {story && <p className="panel-subtitle">{story.title} · {story.summary}</p>}
      <p><strong>Say:</strong> “{step.read}”</p>
      <p className="fine-print">{step.detail}</p>
      {step.story && !story && !storyError && <div className="fine-print" role="status">Retrying the named synthetic story…</div>}
      <Link className="btn primary" href={openHref}>Open {story?.title || step.title}</Link>
      <nav className="tour-navigation" aria-label="Guided tour navigation">
        <button className="btn" disabled={activeIndex === 0} onClick={() => setStop(activeIndex - 1)}>← Previous stop</button>
        <div className="tour-progress">{steps.map((item, index) => <button key={item.title} className={index === activeIndex ? "active" : ""} aria-label={`Go to stop ${index + 1}: ${item.title}`} aria-current={index === activeIndex ? "step" : undefined} onClick={() => setStop(index)}>{index + 1}</button>)}</div>
        <button className="btn" disabled={activeIndex === steps.length - 1} onClick={() => setStop(activeIndex + 1)}>Next stop →</button>
      </nav>
    </section>
  </>;
}
