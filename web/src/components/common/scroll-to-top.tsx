import { ActionIcon } from "@mantine/core";
import { IconArrowUp } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

/** 无限滚动海报墙的返回顶部入口: 滚过一屏后出现, 键盘可达. */
export function ScrollToTop() {
  const { t } = useTranslation("common");
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const update = () => setVisible(window.scrollY > window.innerHeight);
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update, { passive: true });
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  if (!visible) return null;

  return (
    <ActionIcon
      variant="filled"
      radius="xl"
      size="lg"
      aria-label={t("actions.backToTop")}
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      style={{ position: "fixed", right: 24, bottom: 24, zIndex: 200 }}
    >
      <IconArrowUp size={18} />
    </ActionIcon>
  );
}
