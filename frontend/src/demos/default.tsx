import { ParallaxComponent } from '@/components/ui/parallax-scrolling';

export default function ParallaxDemo() {
  return (
    <>
      <ParallaxComponent />
      <div className="bg-black py-12 border-t border-white/10 flex justify-center text-center">
        <p className="text-gray-500 text-sm">
          Resource by <a target="_blank" rel="noreferrer" href="https://www.osmo.supply/" className="text-white hover:underline transition-colors font-medium">Osmo</a>
        </p>
      </div>
    </>
  );
}
