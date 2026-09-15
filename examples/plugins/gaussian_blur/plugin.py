"""Minimal local node plugin example."""


def healthcheck():
    try:
        import cv2  # noqa: F401
    except Exception as exc:
        return f"OpenCV is unavailable: {exc}"
    return "OpenCV is available"


def build_node_class():
    from annoweave.workflow.node import Node
    from annoweave.workflow.packet import Packet

    class GaussianBlurNode(Node):
        Meta = {
            "type_name": "plugin_gaussian_blur",
            "display_name": "Gaussian blur (plugin)",
            "description": "Blur the current frame without changing annotation coordinates",
            "param_schema": {"kernel": {"label": "Kernel size", "type": "int", "default": 5}},
            "tags": ["plugin", "image"],
            "category": "Image processing",
        }

        def run(self, packet: Packet) -> Packet:
            if packet.frame is None or packet.frame.image is None:
                return packet
            import cv2
            kernel = max(1, int(self.get("kernel", 5)))
            kernel += int(kernel % 2 == 0)
            packet.frame.image = cv2.GaussianBlur(packet.frame.image, (kernel, kernel), 0)
            packet.meta["plugin_blur_kernel"] = kernel
            return packet

    return GaussianBlurNode


def register(host):
    host.register_node("plugin_gaussian_blur", build_node_class())
