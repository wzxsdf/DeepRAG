import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { RootLayout } from "@/components/layout/RootLayout"
import { ProtectedRoute } from "@/components/layout/ProtectedRoute"
import Login from "@/pages/Login"
import Register from "@/pages/Register"
import Chat from "@/pages/Chat"
import KnowledgeBase from "@/pages/KnowledgeBase"
import Monitor from "@/pages/Monitor"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route
          element={
            <ProtectedRoute>
              <RootLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/chat" element={<Chat />} />
          <Route path="/kb" element={<KnowledgeBase />} />
          <Route path="/monitor" element={<Monitor />} />
        </Route>
        <Route path="*" element={<Navigate to="/chat" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
