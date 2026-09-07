import React from "react";
import { createRoot } from "react-dom/client";
import { lazy, Suspense } from "react";
const App = lazy(() =>
  import.meta.env.MODE === "pages" ? import("./web/PagesApp") : import("./App"),
);
import "./styles.css";

type BoundaryState = { error: Error | null };

class AppErrorBoundary extends React.Component<
  React.PropsWithChildren,
  BoundaryState
> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("课堂助手界面渲染失败", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="boot-error-page">
          <h2>课堂助手界面加载失败</h2>
          <p>页面组件遇到异常，请重新加载。已保存的数据不会因此删除。</p>
          <button className="primary" onClick={() => window.location.reload()}>
            重新加载页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <AppErrorBoundary>
    <Suspense fallback={<div className="empty">正在加载…</div>}>
      <App />
    </Suspense>
  </AppErrorBoundary>,
);
