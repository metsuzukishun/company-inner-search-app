"""
このファイルは、最初の画面読み込み時にのみ実行される初期化処理が記述されたファイルです。
"""

############################################################
# ライブラリの読み込み
############################################################
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from uuid import uuid4
import sys
import unicodedata
from dotenv import load_dotenv
import streamlit as st
from docx import Document
from langchain_community.document_loaders import WebBaseLoader
# 定数ファイル経由でLoaderをインポートしていない場合の保険
from langchain_community.document_loaders import PyMuPDFLoader, Docx2txtLoader, TextLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
import constants as ct

############################################################
# 設定関連
############################################################
load_dotenv()

############################################################
# 関数定義
############################################################

def initialize():
    """
    画面読み込み時に実行する初期化処理
    """
    initialize_session_state()
    initialize_session_id()
    initialize_logger()
    initialize_retriever()

def initialize_logger():
    """ログ出力の設定"""
    os.makedirs(ct.LOG_DIR_PATH, exist_ok=True)
    logger = logging.getLogger(ct.LOGGER_NAME)
    if logger.hasHandlers():
        return
    log_handler = TimedRotatingFileHandler(
        os.path.join(ct.LOG_DIR_PATH, ct.LOG_FILE),
        when="D",
        encoding="utf8"
    )
    formatter = logging.Formatter(
        f"[%(levelname)s] %(asctime)s line %(lineno)s, in %(funcName)s, session_id={st.session_state.session_id}: %(message)s"
    )
    log_handler.setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)

def initialize_session_id():
    """セッションIDの作成"""
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid4().hex

def initialize_session_state():
    """初期化データの用意"""
    if "messages" not in st.session_state:
        st.session_state.messages = []
        st.session_state.chat_history = []

# ★★★ ここが修正ポイント：重い処理をキャッシュ化する関数 ★★★
@st.cache_resource(show_spinner=False)
def get_vectorstore():
    """
    重い処理（ファイル読み込みとDB作成）をキャッシュする
    """
    # データソースの読み込み
    docs_all = load_data_sources()

    # 文字列調整
    for doc in docs_all:
        doc.page_content = adjust_string(doc.page_content)
        for key in doc.metadata:
            doc.metadata[key] = adjust_string(doc.metadata[key])
    
    # 埋め込みモデル
    embeddings = OpenAIEmbeddings()
    
    # チャンク分割
    text_splitter = CharacterTextSplitter(
        chunk_size=ct.CHUNK_SIZE,
        chunk_overlap=ct.CHUNK_OVERLAP,
        separator="\n"
    )
    splitted_docs = text_splitter.split_documents(docs_all)

    # ベクターストア作成 (ここが一番メモリを使う)
    db = Chroma.from_documents(splitted_docs, embedding=embeddings)
    return db

def initialize_retriever():
    """
    Retrieverの作成
    """
    logger = logging.getLogger(ct.LOGGER_NAME)
    if "retriever" in st.session_state:
        return
    
    # キャッシュされたDBを取得（2回目以降は爆速になり、メモリも節約される）
    try:
        db = get_vectorstore()
        st.session_state.retriever = db.as_retriever(search_kwargs={"k": ct.RETRIEVER_K})
    except Exception as e:
        logger.error(f"Failed to initialize retriever: {e}")
        st.error("データベースの作成に失敗しました。メモリ不足の可能性があります。")


# --- 以下、ファイル読み込み系関数はそのまま ---

def load_data_sources():
    docs_all = []
    recursive_file_check(ct.RAG_TOP_FOLDER_PATH, docs_all)
    # Web読込は省略または必要に応じて追加
    return docs_all

def recursive_file_check(path, docs_all):
    if os.path.isdir(path):
        files = os.listdir(path)
        for file in files:
            full_path = os.path.join(path, file)
            recursive_file_check(full_path, docs_all)
    else:
        file_load(path, docs_all)

def file_load(path, docs_all):
    file_extension = os.path.splitext(path)[1]
    if file_extension in ct.SUPPORTED_EXTENSIONS:
        # Loaderのクラスを取得してインスタンス化
        loader_cls = ct.SUPPORTED_EXTENSIONS[file_extension]
        # lambda対応: 定数側でlambda定義している場合はそのまま呼ぶ
        try:
            loader = loader_cls(path)
        except:
            # 引数が合わない場合などはそのまま実行（lambda定義済みの場合など）
            loader = loader_cls(path)
            
        docs = loader.load()
        docs_all.extend(docs)

def adjust_string(s):
    if type(s) is not str:
        return s
    if sys.platform.startswith("win"):
        s = unicodedata.normalize('NFC', s)
        try:
            s = s.encode("cp932", "ignore").decode("cp932")
        except:
            pass # エラー時は何もしない
        return s
    return s