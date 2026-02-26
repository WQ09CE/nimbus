import { useCopilotChatInternal } from "@copilotkit/react-core";
export default function Test() {
  const chat = useCopilotChatInternal();
  return <div>{chat.messages.length}</div>;
}
