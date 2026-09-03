import type { ComponentProps } from "react";
import { QaOverlay } from "./QaWorkspace";

export function QaDeleteDialog(props: ComponentProps<typeof QaOverlay>) {
  return <QaOverlay {...props} />;
}
