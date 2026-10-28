import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  addEdge,
  reconnectEdge,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

function CardNode({ data }) {
  return (
    <div className={`ex-card ${data.side} ${data.mark || ""}`}>
      {data.side === "left" ? <Handle type="source" position={Position.Right} /> : null}
      {data.side === "right" ? <Handle type="target" position={Position.Left} /> : null}
      <p>{data.label}</p>
    </div>
  );
}

function DiagramNode({ data }) {
  return (
    <div className={`ex-card diagram ${data.mark || ""} ${data.picked ? "picked" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <p>{data.label}</p>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const nodeTypes = { card: CardNode, diagram: DiagramNode };

const ROW = 132;

function boardNodes(spec) {
  const left = (spec.left || []).map((item, index) => ({
    id: item.id,
    type: "card",
    position: { x: 16, y: index * ROW },
    data: { label: item.label, side: "left", mark: "" },
    draggable: false,
    selectable: false,
    sourcePosition: Position.Right,
  }));
  const right = (spec.right || []).map((item, index) => ({
    id: item.id,
    type: "card",
    position: { x: 520, y: index * ROW },
    data: { label: item.label, side: "right", mark: "" },
    draggable: false,
    selectable: false,
    targetPosition: Position.Left,
  }));
  return [...left, ...right];
}

function boardHeight(spec) {
  const rows = Math.max((spec.left || []).length, (spec.right || []).length, 1);
  return rows * ROW + 28;
}

function FitBoard({ onHeight, height }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const measure = () => {
      const wrap = document.querySelector(".flow-wrap");
      const cards = [...document.querySelectorAll(".react-flow__node")];
      if (!wrap || cards.length === 0) return;
      let naturalWidth = 0;
      let naturalHeight = 0;
      for (const card of cards) {
        const match = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(card.style.transform || "");
        const x = match ? Number(match[1]) : 0;
        const y = match ? Number(match[2]) : 0;
        naturalWidth = Math.max(naturalWidth, x + card.offsetWidth);
        naturalHeight = Math.max(naturalHeight, y + card.offsetHeight);
      }
      const scale = Math.min(1, Math.max(0.3, (wrap.clientWidth - 8) / (naturalWidth + 16)));
      onHeight(Math.ceil(naturalHeight * scale + 16));
    };
    const frame = requestAnimationFrame(measure);
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
    };
  }, [onHeight]);
  useEffect(() => {
    const frame = requestAnimationFrame(() => fitView({ padding: 0.02, minZoom: 0.3, maxZoom: 1, duration: 0 }));
    return () => cancelAnimationFrame(frame);
  }, [fitView, height]);
  return null;
}

function ExerciseApp({ spec, exerciseId, csrf }) {
  const initial = useMemo(() => boardNodes(spec), [spec]);
  const [nodes, setNodes, applyNodeChanges] = useNodesState(initial);
  const [height, setHeight] = useState(() => boardHeight(spec));
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [answers, setAnswers] = useState({});
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function onNodesChange(changes) {
    applyNodeChanges(changes.filter((change) => change.type !== "position"));
  }

  function acceptLink(connection) {
    if (!connection.source || !connection.target) return false;
    const source = nodes.find((node) => node.id === connection.source);
    const target = nodes.find((node) => node.id === connection.target);
    return Boolean(source && target && source.data.side === "left" && target.data.side === "right");
  }

  function onConnect(connection) {
    if (!acceptLink(connection)) return;
    setEdges((current) =>
      addEdge(
        { ...connection, id: connection.source },
        current.filter((edge) => edge.source !== connection.source),
      ),
    );
  }

  function onReconnect(oldEdge, connection) {
    if (!acceptLink(connection)) return;
    setEdges((current) =>
      reconnectEdge(oldEdge, { ...connection, id: connection.source }, current).filter(
        (edge) => edge.source !== connection.source || edge.id === connection.source,
      ),
    );
  }

  async function check() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/exercises/${exerciseId}/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({
          links: edges.map((edge) => ({ source: edge.source, target: edge.target })),
          answers,
        }),
      });
      if (!response.ok) {
        setError("The answers could not be checked.");
        return;
      }
      const grade = await response.json();
      setResult(grade);
      const wrong = new Set(grade.match.wrong || []);
      const linked = new Set(edges.map((edge) => edge.source));
      setNodes((current) =>
        current.map((node) => {
          if (node.data.side !== "left") return node;
          let mark = "";
          if (linked.has(node.id)) mark = wrong.has(node.id) ? "bad" : "ok";
          return { ...node, data: { ...node.data, mark } };
        }),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="flow-wrap" style={{ height }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onReconnect={onReconnect}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.04, minZoom: 0.35, maxZoom: 1 }}
          nodesDraggable={false}
          nodesConnectable
          edgesReconnectable
          elementsSelectable
          selectNodesOnDrag={false}
          panOnDrag={false}
          panOnScroll={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          zoomActivationKeyCode={null}
          panActivationKeyCode={null}
          autoPanOnConnect={false}
          preventScrolling={false}
          proOptions={{ hideAttribution: false }}
        >
          <FitBoard onHeight={setHeight} height={height} />
          <Background />
        </ReactFlow>
      </div>
      <div className="questions">
        {(spec.questions || []).map((question, index) => {
          const verdict = result ? result.questions[question.id] : null;
          return (
            <fieldset key={question.id}>
              <legend>
                {index + 1}. {question.prompt}
                {verdict === true ? <span className="q-ok"> Correct</span> : null}
                {verdict === false ? <span className="q-bad"> Try again</span> : null}
              </legend>
              {question.choices.map((choice, choiceIndex) => (
                <label key={`${question.id}-${choiceIndex}`}>
                  <input
                    type="radio"
                    name={question.id}
                    checked={answers[question.id] === choiceIndex}
                    onChange={() => setAnswers((current) => ({ ...current, [question.id]: choiceIndex }))}
                  />
                  <span>{choice}</span>
                </label>
              ))}
            </fieldset>
          );
        })}
      </div>
      <p>
        <button type="button" onClick={check} disabled={busy}>
          {busy ? "Checking" : "Check answers"}
        </button>
      </p>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {result ? (
        <p className="notice" role="status">
          Matches {result.match.correct} of {result.match.total}. Questions {result.questionsCorrect} of {result.questionsTotal}.
          {result.passed ? " Completed." : " Adjust the lines or answers and check again."}
        </p>
      ) : null}
    </div>
  );
}

function DiagramExercise({ spec, exerciseId, csrf }) {
  const initial = useMemo(
    () =>
      (spec.nodes || []).map((node) => ({
        id: node.id,
        type: "diagram",
        position: { x: node.x || 0, y: node.y || 0 },
        data: { label: node.label, mark: "" },
        draggable: false,
        selectable: false,
      })),
    [spec],
  );
  const [nodes, setNodes, applyNodeChanges] = useNodesState(initial);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [height, setHeight] = useState(640);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState(null);

  function onNodesChange(changes) {
    applyNodeChanges(changes.filter((change) => change.type !== "position"));
  }

  function onNodeClick(_event, node) {
    setPicked(node.id);
    setNodes((current) =>
      current.map((item) => ({ ...item, data: { ...item.data, picked: item.id === node.id } })),
    );
  }

  function onConnect(connection) {
    if (!connection.source || !connection.target || connection.source === connection.target) return;
    setEdges((current) => {
      if (current.some((edge) => edge.source === connection.source && edge.target === connection.target)) return current;
      return addEdge({ ...connection, id: `${connection.source}-${connection.target}` }, current);
    });
  }

  function onReconnect(oldEdge, connection) {
    if (!connection.source || !connection.target || connection.source === connection.target) return;
    setEdges((current) =>
      reconnectEdge(oldEdge, { ...connection, id: `${connection.source}-${connection.target}` }, current),
    );
  }

  async function check() {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/exercises/${exerciseId}/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({
          links: edges.map((edge) => ({ source: edge.source, target: edge.target })),
          answers: {},
        }),
      });
      if (!response.ok) {
        setError("The diagram could not be checked.");
        return;
      }
      const grade = await response.json();
      setResult(grade);
      const wrong = new Set(grade.match.wrong || []);
      const used = new Set(edges.flatMap((edge) => [edge.source, edge.target]));
      setNodes((current) =>
        current.map((node) => {
          let mark = "";
          if (wrong.has(node.id)) mark = "bad";
          else if (used.has(node.id)) mark = "ok";
          return { ...node, data: { ...node.data, mark } };
        }),
      );
    } finally {
      setBusy(false);
    }
  }

  const tallest = Math.max(0, ...(spec.nodes || []).map((node) => node.y || 0));
  return (
    <div>
      <div className="flow-wrap" style={{ height: Math.max(height, tallest + 160) }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onConnect={onConnect}
          onReconnect={onReconnect}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.08, minZoom: 0.25, maxZoom: 1 }}
          nodesDraggable={false}
          nodesConnectable
          edgesReconnectable
          elementsSelectable
          selectNodesOnDrag={false}
          panOnDrag={false}
          panOnScroll={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          zoomActivationKeyCode={null}
          panActivationKeyCode={null}
          autoPanOnConnect={false}
          preventScrolling={false}
          proOptions={{ hideAttribution: false }}
        >
          <FitBoard onHeight={setHeight} height={height} />
          <Background />
        </ReactFlow>
      </div>
      <section className="card block-explain" id="block-explain">
        {picked ? (
          <>
            <h2>{(spec.nodes || []).find((node) => node.id === picked)?.label}</h2>
            <p>{(spec.nodes || []).find((node) => node.id === picked)?.explain}</p>
          </>
        ) : (
          <p>Click a block to read what it does. The explanation stays under the diagram.</p>
        )}
      </section>
      <p>
        <button type="button" onClick={check} disabled={busy}>
          {busy ? "Checking" : "Check diagram"}
        </button>
      </p>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {result ? (
        <p className="notice" role="status">
          Lines {result.match.correct} of {result.match.total}.
          {result.passed ? " The diagram is complete." : " Adjust the lines and check again."}
        </p>
      ) : null}
    </div>
  );
}

const root = document.getElementById("exercise-root");
const specNode = document.getElementById("exercise-spec");
if (root && specNode) {
  const spec = JSON.parse(specNode.textContent);
  const Board = spec.kind === "diagram" ? DiagramExercise : ExerciseApp;
  createRoot(root).render(<Board spec={spec} exerciseId={root.dataset.id} csrf={root.dataset.csrf} />);
}
