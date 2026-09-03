import type { ComponentProps } from "react";
import { QaOverlay } from "./QaWorkspace";

export function QaDrawer(props: ComponentProps<typeof QaOverlay>) {
  return <QaOverlay {...props} />;
}
