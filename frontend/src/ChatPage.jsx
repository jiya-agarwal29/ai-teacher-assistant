import { useState } from "react";

function ChatPage() {

    const [messages, setMessages] = useState([
        {
            role: "assistant",
            content:
                "Hello 👋 I am your AI Teacher Assistant. Ask me anything."
        }
    ]);

    const [question, setQuestion] = useState("");

    const sendMessage = () => {

        if (!question.trim()) return;

        setMessages([
            ...messages,

            {
                role: "user",
                content: question
            },

            {
                role: "assistant",
                content:
                    "This is a sample AI response for now."
            }
        ]);

        setQuestion("");
    };

    return (

        <div className="h-screen flex bg-[#020617] text-white overflow-hidden">

            {/* Sidebar */}
            <div className="w-72 bg-[#0F172A]/80 backdrop-blur-xl border-r border-white/10 p-6">

                <h1 className="text-3xl font-bold bg-gradient-to-r from-cyan-400 to-blue-500 bg-clip-text text-transparent">

                    AI Teacher

                </h1>

                <div className="mt-10 space-y-4">

                    <button className="w-full text-left px-5 py-4 rounded-2xl bg-white/10 hover:bg-white/15 transition">

                        💬 New Chat

                    </button>

                    <button className="w-full text-left px-5 py-4 rounded-2xl bg-white/5 hover:bg-white/10 transition">

                        📚 Documents

                    </button>

                    <button className="w-full text-left px-5 py-4 rounded-2xl bg-white/5 hover:bg-white/10 transition">

                        🧠 AI Tools

                    </button>

                </div>

            </div>

            {/* Main Chat Area */}
            <div className="flex-1 flex flex-col relative">

                {/* Background Glow */}
                <div className="absolute inset-0 overflow-hidden -z-10">

                    <div className="absolute top-[-200px] left-[-200px] w-[500px] h-[500px] bg-cyan-500/20 rounded-full blur-[120px]"></div>

                    <div className="absolute bottom-[-200px] right-[-200px] w-[500px] h-[500px] bg-blue-500/20 rounded-full blur-[120px]"></div>

                </div>

                {/* Navbar */}
                <div className="h-20 border-b border-white/10 bg-[#0F172A]/60 backdrop-blur-xl flex items-center justify-between px-8">

                    <h2 className="text-2xl font-semibold">

                        AI Chat Assistant
                    </h2>

                    <div className="w-11 h-11 rounded-full bg-gradient-to-r from-cyan-500 to-blue-500"></div>

                </div>

                {/* Chat Messages */}
                <div className="flex-1 overflow-y-auto px-10 py-10 space-y-8">

                    {
                        messages.map((msg, index) => (

                            <div
                                key={index}

                                className={`flex ${
                                    msg.role === "user"
                                        ? "justify-end"
                                        : "justify-start"
                                }`}
                            >

                                <div
                                    className={`max-w-[750px] px-7 py-5 rounded-3xl border shadow-2xl ${
                                        msg.role === "user"

                                            ? "bg-gradient-to-r from-cyan-500 to-blue-500 border-cyan-400/30"

                                            : "bg-white/10 border-white/10 backdrop-blur-xl"
                                    }`}
                                >

                                    <p className="text-lg leading-relaxed">

                                        {msg.content}

                                    </p>

                                </div>

                            </div>
                        ))
                    }

                </div>

                {/* Input Area */}
                <div className="p-8 border-t border-white/10 bg-[#0F172A]/50 backdrop-blur-xl">

                    <div className="flex items-center gap-4 bg-white/10 border border-white/10 rounded-3xl px-6 py-4">

                        <input
                            type="text"

                            value={question}

                            onChange={(e) =>
                                setQuestion(e.target.value)
                            }

                            placeholder="Ask anything..."

                            className="flex-1 bg-transparent outline-none text-lg text-white"
                        />

                        <button
                            onClick={sendMessage}

                            className="px-7 py-3 rounded-2xl bg-gradient-to-r from-cyan-500 to-blue-500 hover:scale-105 transition duration-300"
                        >

                            Send

                        </button>

                    </div>

                </div>

            </div>

        </div>
    );
}

export default ChatPage;