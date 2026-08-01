import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChatPanel } from "./ChatPanel";
import { Composer } from "./Composer";
import type { ActiveObject } from "../lib/types";

const activeObject: ActiveObject = {
  kind: "strategy",
  id: "route-1",
  title: "Lock the claim before deeper strategy work",
};

describe("active object UI", () => {
  it("shows active context in the composer", () => {
    const html = renderToStaticMarkup(
      <Composer
        runId="run-1"
        onRunCreated={() => undefined}
        onChatMessage={async () => undefined}
        chatBusy={false}
        activeObject={activeObject}
        onClearActiveObject={() => undefined}
      />,
    );
    expect(html).toContain("Active strategy");
    expect(html).toContain("Lock the claim before deeper strategy work");
    // The composer is now a single Send lane bound to one problem;
    // the active object shows in the placeholder as "Continue about ...".
    expect(html).toContain("Continue about strategy");
  });

  it("records active context in the conversation preview", () => {
    const html = renderToStaticMarkup(
      <ChatPanel
        runId="run-1"
        activeObject={activeObject}
        lastNote="What blocks this route?"
        messages={[
          {
            id: 1,
            role: "you",
            text: "What blocks this route?",
            activeObject,
          },
        ]}
      />,
    );
    expect(html).toContain("Context requested: active strategy");
    expect(html).toContain("Lock the claim before deeper strategy work");
    expect(html).toContain("Note on active object");
    expect(html).not.toContain("text-sage");
  });
});
