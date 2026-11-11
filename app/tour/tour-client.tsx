"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { requestJson } from "../lib/api";

type DemoStory = { key: string; title: string; summary: string; href: string };

const steps = [
  {
    title: "Portfolio",
    read: "Open balance, past-due invoices, and current client timing signals.",
    detail: "Start with the open-book headline, then use the aging and cash horizon rows to see what the model can and cannot place.",
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
    detail: "Review the reminder draft and the separate 90+ day collections lane. Synthetic approvals are simulations.",
    href: "/actions",
  },
  {
    title: "Model performance",
    read: "The release model is named against its validation selection rule and held-out results.",
    detail: "Show censor-aware scores, calibration drift, the model card, ablations, and the independent public-data evaluation.",
    href: "/performance",
  },
  {
    title: "Reply triage",
    read: "A promise changes the forecast as a labeled scenario; a dispute pauses reminders.",
    detail: "Open the synthetic dispute story and inspect the reply-triage panel. Classifying a reply is a demo mutation.",
    story: "dispute",
  },
];

export default function TourClient() {
  const [stories, setStories] = useState<DemoStory[]>([]);
  const [storyError, setStoryError] = useState("");
  useEffect(() => {
    let active = true;
    void requestJson<{ items: DemoStory[] }>("/api/demo-stories")
      .then(result => { if (active) setStories(result.items); })
      .catch(() => { if (active) setStoryError("Named synthetic story links are temporarily unavailable."); });
    return () => { active = false; };
  }, []);
  return <>
    <div className="heading-row"><div><div className="eyebrow">Three-minute route · five stops</div><h1>Guided demo tour</h1><p className="subheading">Use these five screens to explain the receivables workflow, its forecast limits, and the human review step.</p></div></div>
    {storyError && <div className="empty-inline" role="status">{storyError}</div>}
    <section className="queue-list" aria-label="Five guided demo stops">
      {steps.map((step, index) => {
        const story = step.story ? stories.find(item => item.key === step.story) : null;
        const href = story?.href || step.href || "/";
        const anchor = step.story === "dispute" ? "#reply-triage" : "";
        return <article className="panel" key={step.title}>
          <div className="eyebrow">Stop {index + 1} of 5 · about 35 seconds</div>
          <h2>{step.title}</h2>
          {story && <p className="panel-subtitle">{story.title} · {story.summary}</p>}
          <p><strong>Say:</strong> “{step.read}”</p>
          <p className="fine-print">{step.detail}</p>
          {step.story && !story
            ? <div className="fine-print" role="status">{storyError || "Loading the named synthetic story link…"}</div>
            : <Link className="btn primary" href={`${href}${anchor}`}>Open {story?.title || step.title}</Link>}
        </article>;
      })}
    </section>
  </>;
}
