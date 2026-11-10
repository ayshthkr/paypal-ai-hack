const DECISION_LABELS: Record<string, string> = {
  friendly: "Friendly reminder",
  firm: "Firm reminder",
  offer_partial_payment: "Offer partial payment",
  personal_call: "Personal call",
  review_for_writeoff_or_collections: "Collections review",
  wait: "Wait",
};

export function decisionLabel(action: string, status?: string, disputed = false): string {
  if (disputed || ["PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"].includes(status || "")) {
    return "No action needed";
  }
  return DECISION_LABELS[action] || action.replaceAll("_", " ");
}

export function statusLabel(status: string, disputed = false): string {
  if (disputed) return "Disputed";
  return status.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, character => character.toUpperCase());
}

export function termLabel(term: string | null | undefined): string {
  if (!term) return "Not specified";
  if (term === "DUE_ON_RECEIPT") return "Due on receipt";
  if (term === "NET_15") return "Net 15";
  if (term === "NET_30") return "Net 30";
  return term.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, character => character.toUpperCase());
}

export function signalLabel(signal: string): string {
  if (signal === "stable") return "Consistent timing";
  if (signal === "habitually_late") return "Habitually late";
  return signal.replaceAll("_", " ").replace(/\b\w/g, character => character.toUpperCase());
}
