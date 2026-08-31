import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import "./markdown-preview.css";

function isExternalHref(href: string | undefined): boolean {
  if (!href) return false;
  try {
    const url = new URL(href, window.location.origin);
    return (url.protocol === "http:" || url.protocol === "https:") && url.origin !== window.location.origin;
  } catch {
    return false;
  }
}

function isSafeHref(href: string): boolean {
  if (!href || href.startsWith("//")) return false;
  try {
    const url = new URL(href, window.location.origin);
    return (url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password;
  } catch {
    return href.startsWith("/") || href.startsWith("#") || href.startsWith("?");
  }
}

export function MarkdownPreview({ markdown }: { markdown: string }) {
  return (
    <div className="markdown-preview">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSanitize]}
        urlTransform={(url) => isSafeHref(url) ? url : ""}
        components={{
          a: ({ href, children, ...props }) => isSafeHref(href ?? "") ? (
            <a {...props} href={href} rel={isExternalHref(href) ? "noopener noreferrer" : undefined}>
              {children}
            </a>
          ) : <>{children}</>,
          pre: ({ children }) => <div className="markdown-preview__code"><pre>{children}</pre></div>,
          table: ({ children }) => <div className="markdown-preview__table"><table>{children}</table></div>,
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
