import hashlib
import os
import streamlit as st
from agent import build_redmine_agent, ask_redmine_agent


def render_app():
    st.set_page_config(
    page_title="Redmine PM Assistant",
    page_icon="🤖",
    layout="wide",
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    if "agent" not in st.session_state:
        st.session_state.agent = None

    if "config_signature" not in st.session_state:
        st.session_state.config_signature = None

    st.title("Redmine PM Assistant")
    st.caption("A tool to assist project managers in Redmine tasks and project management.")

    st.sidebar.header("Connection Settings")

    provider_label = st.sidebar.selectbox(
        "LLM Provider",
        options=["Local LLM", "OpenAI"],
    )

    if provider_label == "OpenAI":
        llm_provider = "openai"
    else:
        llm_provider = "local"

    llm_api_key = ""

    if llm_provider == "openai":
        llm_api_key = st.sidebar.text_input(
            "OpenAI API Key",
            type="password",
            placeholder="Enter your OpenAI API key here",
        ).strip()


    redmine_url = st.sidebar.text_input(
        "Redmine URL",
        value="https://redmine.rolustech.com/",
    ).strip()



    redmine_api_key = st.sidebar.text_input(
        "Redmine API Key",
        type="password",
        placeholder="Enter your Redmine API key here",
    ).strip()

    missing_fields = []

    if not redmine_url:
        missing_fields.append("Redmine URL")
    if not redmine_api_key:
        missing_fields.append("Redmine API Key")

    if llm_provider == "openai" and not llm_api_key:
        missing_fields.append("OpenAI API Key")

    if missing_fields:
        st.sidebar.warning(
            "Missing: " + ", ".join(missing_fields)
        )
    else:
        st.sidebar.success("All connection values provided.")


    config_ready = not missing_fields


    if config_ready:
        os.environ["REDMINE_URL"] = redmine_url.rstrip("/")
        os.environ["REDMINE_API_KEY"] = redmine_api_key
        os.environ["LLM_PROVIDER"] = llm_provider
        if llm_provider == "openai":
            os.environ["OPENAI_API_KEY"] = llm_api_key
            os.environ["OPENAI_MODEL"] = "gpt-5.5"
        else:
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("OPENAI_MODEL", None)


    if config_ready:
        current_config_signature = (
            llm_provider,
            redmine_url.rstrip("/"),
            hashlib.sha256(redmine_api_key.encode("utf-8")).hexdigest(),
            hashlib.sha256(llm_api_key.encode("utf-8")).hexdigest(),
        )

        if st.session_state.config_signature != current_config_signature:
            st.session_state.agent = None
            st.session_state.config_signature = current_config_signature
            st.sidebar.info("Configuration changed. Redmine Agent will be re-initialized.")
    else:
        st.session_state.agent = None
        st.session_state.config_signature = None


    if config_ready and st.session_state.agent is None:
        try:
            with st.spinner("Initializing Redmine Agent..."):
                st.session_state.agent = build_redmine_agent()
        except Exception as e:
            st.session_state.agent = None
            st.sidebar.error(f"Error initializing Redmine Agent: {e}")

    agent_ready = st.session_state.agent is not None
    if agent_ready:
        st.sidebar.success("Redmine Agent is ready.")


    if st.sidebar.button(
        "Reset chat",
        use_container_width=True
    ):
        st.session_state.messages = []
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(
        "Ask me anything about your Redmine tasks or project management...",
        disabled=not agent_ready
    )
    if prompt:
        st.session_state.messages.append(
            {
                "role": "user",
                "content": prompt
            }
        )
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    answer = ask_redmine_agent(st.session_state.agent, st.session_state.messages)
                except Exception as e:
                    answer = f"Error: {e}"
            st.markdown(answer)
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer
            }
        )