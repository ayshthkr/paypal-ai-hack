import ActionQueue from "../actions/queue-client";

import SiteHeader from "../site-header";
export default function CollectionsPage() {
  return <div className="app-shell"><SiteHeader/><main className="main-wrap"><ActionQueue collectionsOnly /></main></div>;
}
